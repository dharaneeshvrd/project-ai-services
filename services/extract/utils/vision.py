"""
Vision LLM helper utilities for image-based extraction.

Mirrors the interface of :mod:`extract.utils.vllm` but sends the image as a
base64-encoded ``image_url`` content block so that any OpenAI-compatible
vision model can process it.

The user message contains both the schema/instruction text and the inline
image; there is no separate system message because many vision models give
better results when everything is in the user turn.

Guided decoding (``guided_json``) is applied exactly as in the text path so
that the vision model's output is constrained to the target schema.
"""

import asyncio
import base64
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

import common.misc_utils as misc_utils
from common.llm_utils import get_vllm_headers
from common.misc_utils import get_logger
from common.error_utils import APIError, ErrorCode

from extract.settings import settings

logger = get_logger("vision_utils")

# Map of accepted image extensions to their MIME types
_EXT_MIME: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}

IMAGE_EXTENSIONS = frozenset(_EXT_MIME.keys())


def get_image_mime_type(path: Path) -> str:
    """Return the MIME type for *path*, defaulting to ``image/jpeg``."""
    return _EXT_MIME.get(path.suffix.lower(), "image/jpeg")


def build_vision_messages(
    image_path: Path,
    normalized_schema: Dict[str, Any],
    few_shot_block: str,
    custom_prompt: Optional[str],
) -> List[Dict[str, Any]]:
    """Assemble the chat messages list for a vision extraction prompt.

    The image is embedded inline as a base64 data URL — no external hosting
    required.  Schema instructions are placed in the user message alongside the
    image so that vision models that ignore system messages still receive them.

    Args:
        image_path:        Path to the staged image file on disk.
        normalized_schema: The normalised JSON schema dict.
        few_shot_block:    Pre-rendered few-shot examples string (may be empty).
        custom_prompt:     Optional per-schema custom instruction appended to the
                           extraction instructions.

    Returns:
        A messages list ready to pass to the ``/v1/chat/completions`` endpoint.
    """
    raw_bytes = image_path.read_bytes()
    b64_data = base64.b64encode(raw_bytes).decode("ascii")
    mime_type = get_image_mime_type(image_path)
    data_url = f"data:{mime_type};base64,{b64_data}"

    schema_str = json.dumps(normalized_schema, ensure_ascii=False)

    # Build the text portion of the user message
    lines: List[str] = [
        "Extract all structured data from the image below.",
        "",
        "Rules:",
        "1. Extract values EXACTLY as they appear in the image (preserve numbers, dates, identifiers verbatim).",
        "2. If a non-required property is not visible in the image, omit it or set it to null. NEVER invent values.",
        "3. Required properties must be populated from the image.",
        "4. Output ONLY the JSON object. Do not add explanations, markdown fences, or any other text.",
    ]
    if custom_prompt:
        lines += ["", custom_prompt]
    lines += [
        "",
        f"JSON schema:\n{schema_str}",
    ]
    if few_shot_block:
        lines += ["", few_shot_block]
    lines += ["", "JSON:"]

    user_text = "\n".join(lines)

    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]


def call_vision_llm(
    messages: List[Dict[str, Any]],
    max_tokens: int,
    normalized_schema: Dict[str, Any],
    vision_endpoint: str,
    vision_model: str,
) -> Dict[str, Any]:
    """Execute a single blocking ``/v1/chat/completions`` call against the vision model.

    Uses ``guided_json`` (constrained decoding) exactly as the text path does so
    that the model output is schema-constrained.

    Returns the raw response JSON dict.
    Raises ``requests.exceptions.ConnectionError`` / ``HTTPError`` on failure.
    """
    if misc_utils.SESSION is None:
        raise RuntimeError("LLM session not initialized.")

    payload: Dict[str, Any] = {
        "model": vision_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": settings.extract.extraction_temperature,
        "stream": False,
    }

    if settings.extract.guided_decoding_enabled:
        payload["extra_body"] = {"guided_json": normalized_schema}

    headers = get_vllm_headers(settings.common.llm.api_key.get_secret_value())
    response = misc_utils.SESSION.post(
        f"{vision_endpoint}/v1/chat/completions",
        json=payload,
        headers=headers,
        stream=False,
    )
    response.raise_for_status()
    return response.json()


async def call_vision_safe(
    messages: List[Dict[str, Any]],
    reserved_output: int,
    json_schema: Dict[str, Any],
    vision_endpoint: str,
    vision_model: str,
) -> Dict[str, Any]:
    """Wrap :func:`call_vision_llm` in ``asyncio.to_thread`` with uniform error handling.

    Args:
        messages:        Vision chat messages list (text + inline image).
        reserved_output: Maximum tokens to reserve for the model output.
        json_schema:     The normalised JSON schema (used for guided decoding).
        vision_endpoint: Base URL of the vision LLM endpoint.
        vision_model:    Model name to pass in the payload.

    Returns:
        The raw vLLM response dict.

    Raises:
        HTTPException(503) on connection failure.
        HTTPException(500) on HTTP error or any other unexpected failure.
    """
    try:
        return await asyncio.to_thread(
            call_vision_llm,
            messages,
            reserved_output,
            json_schema,
            vision_endpoint,
            vision_model,
        )
    except requests.exceptions.ConnectionError as exc:
        logger.error(f"Vision LLM unreachable: {exc}")
        APIError.raise_error(ErrorCode.LLM_UNAVAILABLE, "The vision AI service is unreachable.")
    except requests.exceptions.HTTPError as exc:
        logger.error(f"Vision LLM HTTP error: {exc}")
        APIError.raise_error(
            ErrorCode.LLM_ERROR,
            f"The vision AI service returned an error: {exc.response.status_code}",
        )
    except Exception as exc:
        logger.error(f"Unexpected error calling vision LLM: {exc}", exc_info=True)
        APIError.raise_error(ErrorCode.LLM_ERROR, "Unexpected error during vision LLM call.")
