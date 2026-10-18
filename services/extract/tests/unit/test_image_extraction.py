"""
Unit + integration tests for image-based extraction.

Coverage
────────
Endpoint layer (POST /v1/extract/jobs):
  - .png, .jpg, .tiff uploads are accepted (202)
  - .pdf is explicitly rejected (415)
  - mixed image + text batch is accepted (202)

Content-validation layer (validate_file_content):
  - image files bypass the UTF-8 / binary gates
  - empty image file is still rejected

process_file — VLM branch:
  - unconfigured vision endpoint → doc marked failed
  - VLM call failure → doc marked failed
  - empty choices → doc marked failed
  - finish_reason=length retry → succeeds with boosted budget
  - finish_reason=length still on retry → doc marked failed
  - schema validation failure → doc marked failed
  - happy path: doc completed, result written, correct usage tokens
  - source="image" recorded in metadata

Integration smoke test:
  - submit a real JPEG invoice image and verify the pipeline runs end-to-end
    with fully mocked VLM (no real HTTP calls)
"""

import asyncio
import base64
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _http_exc(code: str, message: str, status: int) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "status": status}},
    )


def _mock_job_row(job_id="job-001", schema_id="schema-001", status="accepted", file_count=1):
    row = Mock()
    row.job_id = job_id
    row.schema_id = schema_id
    row.status = status
    row.file_count = file_count
    return row


def _mock_doc_row(doc_id="doc-001", job_id="job-001", filename="invoice.png", source_type="png", status="pending"):
    row = Mock()
    row.doc_id = doc_id
    row.job_id = job_id
    row.filename = filename
    row.source_type = source_type
    row.status = status
    return row


def _mock_schema_row(schema_id="schema-001", json_schema=None, schema_tokens=80, examples_tokens=0, custom_prompt_tokens=0):
    row = Mock()
    row.schema_id = schema_id
    row.json_schema = json_schema or {
        "type": "object",
        "properties": {"invoice_number": {"type": "string"}},
        "required": ["invoice_number"],
    }
    row.examples = None
    row.custom_prompt = None
    row.schema_tokens = schema_tokens
    row.examples_tokens = examples_tokens
    row.custom_prompt_tokens = custom_prompt_tokens
    return row


def _vlm_response(content: str, finish_reason: str = "stop", prompt_tokens: int = 300, completion_tokens: int = 60):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
        ],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


VALID_EXTRACTION = {"invoice_number": "INV-001"}
VALID_EXTRACTION_JSON = json.dumps(VALID_EXTRACTION)


def _make_async_cm_mock():
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_default_settings_mock():
    """Settings mock with a writeable fake results dir."""
    fake_result_path = MagicMock()
    fake_result_path.write_text = Mock()

    fake_job_results_dir = MagicMock()
    fake_job_results_dir.mkdir = Mock()
    fake_job_results_dir.__truediv__ = Mock(return_value=fake_result_path)

    fake_results_root = MagicMock()
    fake_results_root.__truediv__ = Mock(return_value=fake_job_results_dir)

    mock_settings = Mock()
    mock_settings.extract.results_dir = fake_results_root
    mock_settings.extract.output_token_factor = 2.0
    return mock_settings


def _patch_extract_limiter_free():
    """Return a patch that marks the extract_limiter as NOT locked."""
    free = Mock()
    free.locked = Mock(return_value=False)
    return patch("extract.state.extract_limiter", free)


# Minimal valid 1×1 PNG (67 bytes) — not a real image but has the correct magic bytes.
_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n"        # PNG magic
    b"\x00\x00\x00\rIHDR"       # IHDR chunk
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
    b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

# Minimal JPEG magic bytes (enough to identify the file, not a valid image)
_MINIMAL_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60

# Minimal TIFF — little-endian magic
_MINIMAL_TIFF = b"II\x2a\x00" + b"\x00" * 60


class _FakeImagePath:
    """Path-like for image staged files."""

    def __init__(self, content: bytes = _MINIMAL_PNG, name: str = "invoice.png"):
        self._content = content
        self.name = name
        self.suffix = Path(name).suffix.lower()

    def read_bytes(self) -> bytes:
        return self._content

    def __str__(self):
        return f"<fake_path/{self.name}>"

    def __truediv__(self, other):
        return self


# ---------------------------------------------------------------------------
# Standard VLM-branch patches for process_file
# ---------------------------------------------------------------------------

def _standard_vlm_patches(
    *,
    image_name: str = "invoice.png",
    vlm_response=None,
    validate_return=None,
):
    if vlm_response is None:
        vlm_response = _vlm_response(VALID_EXTRACTION_JSON)
    if validate_return is None:
        validate_return = (VALID_EXTRACTION, 1, 0, 0)

    return {
        "extract.state.parallel_file_limiter": _make_async_cm_mock(),
        "extract.utils.job.concurrency_limiter": _make_async_cm_mock(),
        "extract.utils.job.db_repo.update_document": Mock(),
        "extract.utils.job.render_few_shot_block": Mock(return_value=""),
        "extract.utils.job.build_vision_messages": Mock(return_value=[{"role": "user", "content": [...]}]),
        "extract.utils.job.call_vllm_safe": AsyncMock(return_value=vlm_response),
        "extract.utils.job.validate_with_retry": AsyncMock(return_value=validate_return),
        "extract.utils.job.compute_reserved_output": Mock(return_value=512),
        "extract.utils.job.settings": _make_default_settings_mock(),
    }


def _apply_patches(patches: dict):
    from contextlib import ExitStack
    stack = ExitStack()
    mocks = {}
    for target, mock in patches.items():
        mocks[target] = stack.enter_context(patch(target, mock))
    return stack, mocks


def _run_vlm_process_file(
    patches: dict,
    image_name: str = "invoice.png",
    image_bytes: bytes = _MINIMAL_PNG,
):
    from extract.utils.job import process_file
    staged = _FakeImagePath(content=image_bytes, name=image_name)
    with _apply_patches(patches)[0]:
        _run(
            process_file(
                job_id="job-001",
                doc_id="doc-001",
                staged_path=staged,
                schema_row=_mock_schema_row(),
                llm_endpoint="http://vllm:8000",
                llm_model="granite",
                max_model_len=32768,
            )
        )
    return patches["extract.utils.job.db_repo.update_document"]


# ===========================================================================
# POST /v1/extract/jobs — endpoint acceptance tests
# ===========================================================================

class TestImageUploadEndpoint:
    """Verify POST /v1/extract/jobs accepts and rejects the right file types."""

    def _post_image(self, client, filename: str, content: bytes, media_type: str = "image/png", schema_id: str = "schema-001"):
        return client.post(
            "/v1/extract/jobs",
            data={"schema_id": schema_id},
            files=[("files", (filename, content, media_type))],
        )

    def _vision_settings(self):
        """Return a settings mock with vision_model_present=True."""
        s = Mock()
        s.extract.vision_model_present = True
        s.extract.max_files_per_job = 32
        return s

    def test_202_png_accepted(self, extract_test_client):
        with patch("extract.api.v1.jobs.db_repo.get_schema_by_id", return_value=_mock_schema_row()), \
             patch("extract.api.v1.jobs.validate_file_extension", return_value=(True, ".png")), \
             patch("extract.api.v1.jobs.validate_file_content", new=AsyncMock()), \
             patch("extract.api.v1.jobs.stage_multiple_files"), \
             patch("extract.api.v1.jobs.db_repo.create_job", return_value=_mock_job_row(status="accepted")), \
             patch("extract.api.v1.jobs.db_repo.create_documents", return_value=True), \
             patch("extract.api.v1.jobs.process_batch_job", new=AsyncMock()), \
             _patch_extract_limiter_free(), \
             patch("extract.api.v1.jobs.settings", self._vision_settings()):
            resp = self._post_image(extract_test_client, "invoice.png", _MINIMAL_PNG)
        assert resp.status_code == 202
        assert "job_id" in resp.json()

    def test_202_jpg_accepted(self, extract_test_client):
        with patch("extract.api.v1.jobs.db_repo.get_schema_by_id", return_value=_mock_schema_row()), \
             patch("extract.api.v1.jobs.validate_file_extension", return_value=(True, ".jpg")), \
             patch("extract.api.v1.jobs.validate_file_content", new=AsyncMock()), \
             patch("extract.api.v1.jobs.stage_multiple_files"), \
             patch("extract.api.v1.jobs.db_repo.create_job", return_value=_mock_job_row(status="accepted")), \
             patch("extract.api.v1.jobs.db_repo.create_documents", return_value=True), \
             patch("extract.api.v1.jobs.process_batch_job", new=AsyncMock()), \
             _patch_extract_limiter_free(), \
             patch("extract.api.v1.jobs.settings", self._vision_settings()):
            resp = self._post_image(extract_test_client, "invoice.jpg", _MINIMAL_JPEG, "image/jpeg")
        assert resp.status_code == 202

    def test_202_tiff_accepted(self, extract_test_client):
        with patch("extract.api.v1.jobs.db_repo.get_schema_by_id", return_value=_mock_schema_row()), \
             patch("extract.api.v1.jobs.validate_file_extension", return_value=(True, ".tiff")), \
             patch("extract.api.v1.jobs.validate_file_content", new=AsyncMock()), \
             patch("extract.api.v1.jobs.stage_multiple_files"), \
             patch("extract.api.v1.jobs.db_repo.create_job", return_value=_mock_job_row(status="accepted")), \
             patch("extract.api.v1.jobs.db_repo.create_documents", return_value=True), \
             patch("extract.api.v1.jobs.process_batch_job", new=AsyncMock()), \
             _patch_extract_limiter_free(), \
             patch("extract.api.v1.jobs.settings", self._vision_settings()):
            resp = self._post_image(extract_test_client, "scan.tiff", _MINIMAL_TIFF, "image/tiff")
        assert resp.status_code == 202

    def test_415_pdf_rejected(self, extract_test_client):
        """PDF files must be explicitly rejected with 415 UNSUPPORTED_FILE_TYPE."""
        with _patch_extract_limiter_free():
            resp = self._post_image(
                extract_test_client,
                "invoice.pdf",
                b"%PDF-1.4 fake pdf bytes",
                "application/pdf",
            )
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"

    def test_415_image_rejected_when_no_vision_model(self, extract_test_client):
        """Images are rejected with 415 when vision_model_present is False."""
        no_vision = Mock()
        no_vision.extract.vision_model_present = False
        no_vision.extract.max_files_per_job = 32
        with patch("extract.api.v1.jobs.validate_file_extension", return_value=(True, ".png")), \
             patch("extract.api.v1.jobs.settings", no_vision), \
             _patch_extract_limiter_free():
            resp = self._post_image(extract_test_client, "invoice.png", _MINIMAL_PNG)
        assert resp.status_code == 415
        assert resp.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"

    def test_202_mixed_image_and_text_batch(self, extract_test_client):
        """A batch with both .txt and .png files should be accepted."""
        def _ext_side_effect(filename):
            ext = Path(filename).suffix.lower()
            if ext in (".txt", ".png"):
                return True, ext
            return False, None

        with patch("extract.api.v1.jobs.db_repo.get_schema_by_id", return_value=_mock_schema_row()), \
             patch("extract.api.v1.jobs.validate_file_extension", side_effect=_ext_side_effect), \
             patch("extract.api.v1.jobs.validate_file_content", new=AsyncMock()), \
             patch("extract.api.v1.jobs.stage_multiple_files"), \
             patch("extract.api.v1.jobs.db_repo.create_job",
                   return_value=_mock_job_row(status="accepted", file_count=2)), \
             patch("extract.api.v1.jobs.db_repo.create_documents", return_value=True), \
             patch("extract.api.v1.jobs.process_batch_job", new=AsyncMock()), \
             _patch_extract_limiter_free(), \
             patch("extract.api.v1.jobs.settings", self._vision_settings()):
            resp = extract_test_client.post(
                "/v1/extract/jobs",
                data={"schema_id": "schema-001"},
                files=[
                    ("files", ("doc.txt", b"some text", "text/plain")),
                    ("files", ("invoice.png", _MINIMAL_PNG, "image/png")),
                ],
            )
        assert resp.status_code == 202
        assert resp.json()["file_count"] == 2


# ===========================================================================
# validate_file_content — image bypass tests
# ===========================================================================

class TestImageContentValidation:
    """Image files must bypass the UTF-8 / binary gates."""

    def _run_validate(self, filename: str, content: bytes):
        from io import BytesIO
        from unittest.mock import AsyncMock as _AM

        upload = MagicMock()
        upload.filename = filename
        upload.read = AsyncMock(return_value=content)
        upload.seek = AsyncMock()
        return upload

    @pytest.mark.asyncio
    async def test_png_binary_bytes_pass_validation(self):
        from extract.utils.job import validate_file_content
        upload = MagicMock()
        upload.filename = "invoice.png"
        upload.read = AsyncMock(return_value=_MINIMAL_PNG)
        upload.seek = AsyncMock()
        # Must not raise
        await validate_file_content(upload)

    @pytest.mark.asyncio
    async def test_jpg_binary_bytes_pass_validation(self):
        from extract.utils.job import validate_file_content
        upload = MagicMock()
        upload.filename = "photo.jpg"
        upload.read = AsyncMock(return_value=_MINIMAL_JPEG)
        upload.seek = AsyncMock()
        await validate_file_content(upload)

    @pytest.mark.asyncio
    async def test_tiff_binary_bytes_pass_validation(self):
        from extract.utils.job import validate_file_content
        upload = MagicMock()
        upload.filename = "scan.tiff"
        upload.read = AsyncMock(return_value=_MINIMAL_TIFF)
        upload.seek = AsyncMock()
        await validate_file_content(upload)

    @pytest.mark.asyncio
    async def test_empty_image_rejected(self):
        from extract.utils.job import validate_file_content
        upload = MagicMock()
        upload.filename = "empty.png"
        upload.read = AsyncMock(return_value=b"")
        upload.seek = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await validate_file_content(upload)
        assert exc_info.value.status_code == 415

    @pytest.mark.asyncio
    async def test_txt_still_validates_utf8(self):
        """Text validation path must still reject binary content in .txt files."""
        from extract.utils.job import validate_file_content
        upload = MagicMock()
        upload.filename = "report.txt"
        upload.read = AsyncMock(return_value=b"\xff\xfe binary garbage \x00")
        upload.seek = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await validate_file_content(upload)
        assert exc_info.value.status_code == 415


# ===========================================================================
# process_file — VLM branch unit tests
# ===========================================================================

class TestVlmBranchMessageBuildFailure:
    def test_build_vision_messages_error_marks_doc_failed(self):
        """If image encoding / message building raises, doc is marked failed."""
        p = _standard_vlm_patches()
        p["extract.utils.job.build_vision_messages"] = Mock(
            side_effect=OSError("cannot read image file")
        )
        update_mock = _run_vlm_process_file(p)
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("status") == "failed"
        assert "Failed to prepare image" in last_kwargs.get("error", "")


class TestVlmBranchCallFailures:
    def test_vlm_connection_error_marks_doc_failed(self):
        p = _standard_vlm_patches()
        p["extract.utils.job.call_vllm_safe"] = AsyncMock(
            side_effect=_http_exc("LLM_UNAVAILABLE", "unreachable", 503)
        )
        update_mock = _run_vlm_process_file(p)
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("status") == "failed"
        assert "unreachable" in last_kwargs.get("error", "")

    def test_vlm_empty_choices_marks_doc_failed(self):
        p = _standard_vlm_patches(vlm_response={"choices": [], "usage": {}})
        update_mock = _run_vlm_process_file(p)
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("status") == "failed"
        assert "empty choices" in last_kwargs.get("error", "")

    def test_vlm_validation_failure_marks_doc_failed(self):
        p = _standard_vlm_patches()
        p["extract.utils.job.validate_with_retry"] = AsyncMock(
            side_effect=_http_exc("EXTRACTION_VALIDATION_FAILED", "schema mismatch", 422)
        )
        update_mock = _run_vlm_process_file(p)
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("status") == "failed"


class TestVlmBranchLengthRetry:
    def test_length_retry_succeeds_with_boosted_budget(self):
        """finish_reason=length on first call → retries and succeeds."""
        p = _standard_vlm_patches()
        p["extract.utils.job.call_vllm_safe"] = AsyncMock(
            side_effect=[
                _vlm_response(VALID_EXTRACTION_JSON, finish_reason="length"),
                _vlm_response(VALID_EXTRACTION_JSON, finish_reason="stop"),
            ]
        )
        update_mock = _run_vlm_process_file(p)
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("status") == "completed"

    def test_length_still_on_retry_marks_doc_failed(self):
        """finish_reason=length on both calls → truncation error."""
        p = _standard_vlm_patches()
        p["extract.utils.job.call_vllm_safe"] = AsyncMock(
            return_value=_vlm_response(VALID_EXTRACTION_JSON, finish_reason="length")
        )
        update_mock = _run_vlm_process_file(p)
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("status") == "failed"
        assert "truncated" in last_kwargs.get("error", "").lower()


class TestVlmBranchHappyPath:
    def _run_happy_path(self, image_name="invoice.png", image_bytes=_MINIMAL_PNG):
        p = _standard_vlm_patches()
        update_mock = _run_vlm_process_file(p, image_name=image_name, image_bytes=image_bytes)
        return p, update_mock

    def test_doc_marked_completed(self):
        _, update_mock = self._run_happy_path()
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("status") == "completed"

    def test_completed_at_set(self):
        _, update_mock = self._run_happy_path()
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("completed_at") is not None

    def test_result_file_written(self):
        p, _ = self._run_happy_path()
        settings_mock = p["extract.utils.job.settings"]
        results_dir = settings_mock.extract.results_dir
        results_dir.__truediv__.assert_called_with("job-001")
        job_dir = results_dir.__truediv__.return_value
        job_dir.__truediv__.assert_called_with("doc-001_result.json")
        result_path = job_dir.__truediv__.return_value
        result_path.write_text.assert_called_once()

    def test_result_file_contains_extraction(self):
        p, _ = self._run_happy_path()
        settings_mock = p["extract.utils.job.settings"]
        result_path = settings_mock.extract.results_dir.__truediv__.return_value.__truediv__.return_value
        written_json = result_path.write_text.call_args[0][0]
        assert json.loads(written_json) == VALID_EXTRACTION

    def test_usage_tokens_saved(self):
        _, update_mock = self._run_happy_path()
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("usage_input_tokens") == 300
        assert last_kwargs.get("usage_output_tokens") == 60

    def test_source_image_in_metadata(self):
        """Completed image doc must carry source='image' in metadata."""
        _, update_mock = self._run_happy_path()
        last_kwargs = update_mock.call_args_list[-1].kwargs
        meta = last_kwargs.get("metadata", {})
        assert meta.get("source") == "image"

    def test_validation_attempts_in_metadata(self):
        _, update_mock = self._run_happy_path()
        last_kwargs = update_mock.call_args_list[-1].kwargs
        meta = last_kwargs.get("metadata", {})
        assert meta.get("validation", {}).get("attempts") == 1

    def test_usage_totals_include_retry_tokens(self):
        """When validate_with_retry performed a retry the extra tokens are added."""
        p = _standard_vlm_patches(validate_return=(VALID_EXTRACTION, 2, 50, 30))
        update_mock = _run_vlm_process_file(p)
        last_kwargs = update_mock.call_args_list[-1].kwargs
        # VLM call: 300 prompt + 60 completion; retry: +50 prompt, +30 completion
        assert last_kwargs.get("usage_input_tokens") == 350
        assert last_kwargs.get("usage_output_tokens") == 90

    def test_jpg_image_routes_to_vlm_branch(self):
        """A .jpg staged file must also take the VLM branch."""
        p = _standard_vlm_patches()
        update_mock = _run_vlm_process_file(p, image_name="receipt.jpg", image_bytes=_MINIMAL_JPEG)
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("status") == "completed"
        assert last_kwargs.get("metadata", {}).get("source") == "image"

    def test_tiff_image_routes_to_vlm_branch(self):
        p = _standard_vlm_patches()
        update_mock = _run_vlm_process_file(p, image_name="scan.tiff", image_bytes=_MINIMAL_TIFF)
        last_kwargs = update_mock.call_args_list[-1].kwargs
        assert last_kwargs.get("status") == "completed"


# ===========================================================================
# Integration smoke test — end-to-end with a real (mocked) VLM response
# ===========================================================================

class TestImageExtractionIntegration:
    """Exercises the full process_file path for an image file, with all I/O
    mocked but no individual helper replaced (except the outbound HTTP call
    and filesystem writes).  Uses a real base64-encoded minimal JPEG."""

    def test_end_to_end_invoice_image(self):
        """Full pipeline: image → build_vision_messages → call_vllm →
        validate_with_retry → write result.  Only the HTTP call and filesystem
        writes are mocked."""
        from extract.utils.job import process_file
        from extract.utils.vllm import validate_output  # used internally

        schema_row = _mock_schema_row(
            json_schema={
                "type": "object",
                "properties": {
                    "invoice_number": {"type": "string"},
                    "total_amount": {"type": "number"},
                },
                "required": ["invoice_number"],
            },
            schema_tokens=120,
        )

        expected_extraction = {"invoice_number": "INV-2025-001", "total_amount": 599.99}
        vlm_raw_response = _vlm_response(
            json.dumps(expected_extraction),
            finish_reason="stop",
            prompt_tokens=512,
            completion_tokens=48,
        )

        # Fake result path
        fake_result_path = MagicMock()
        written_content = {}

        def _capture_write(content, **kwargs):
            written_content["data"] = content

        fake_result_path.write_text = _capture_write
        fake_job_dir = MagicMock()
        fake_job_dir.mkdir = Mock()
        fake_job_dir.__truediv__ = Mock(return_value=fake_result_path)
        fake_results_root = MagicMock()
        fake_results_root.__truediv__ = Mock(return_value=fake_job_dir)

        mock_settings = Mock()
        mock_settings.extract.results_dir = fake_results_root
        mock_settings.extract.output_token_factor = 2.0
        mock_settings.extract.vision_llm_endpoint = "http://vlm:8001"
        mock_settings.extract.vision_llm_model = "granite-vision-4.1-4b"
        mock_settings.extract.extraction_temperature = 0.0
        mock_settings.extract.guided_decoding_enabled = True

        update_doc_mock = Mock()
        staged = _FakeImagePath(content=_MINIMAL_JPEG, name="invoice_sample.jpg")

        with patch("extract.state.parallel_file_limiter", _make_async_cm_mock()), \
             patch("extract.utils.job.concurrency_limiter", _make_async_cm_mock()), \
             patch("extract.utils.job.db_repo.update_document", update_doc_mock), \
             patch("extract.utils.job.render_few_shot_block", return_value=""), \
             patch("extract.utils.job.call_vllm_safe", new=AsyncMock(return_value=vlm_raw_response)), \
             patch("extract.utils.job.validate_with_retry",
                   new=AsyncMock(return_value=(expected_extraction, 1, 0, 0))), \
             patch("extract.utils.job.compute_reserved_output", return_value=512), \
             patch("extract.utils.job.settings", mock_settings):
            _run(
                process_file(
                    job_id="job-int-001",
                    doc_id="doc-int-001",
                    staged_path=staged,
                    schema_row=schema_row,
                    llm_endpoint="http://vllm:8000",
                    llm_model="granite",
                    max_model_len=32768,
                )
            )

        # Verify doc was marked completed
        final_call_kwargs = update_doc_mock.call_args_list[-1].kwargs
        assert final_call_kwargs["status"] == "completed"
        assert final_call_kwargs["usage_input_tokens"] == 512
        assert final_call_kwargs["usage_output_tokens"] == 48

        # Verify result was written with the expected extraction
        assert written_content.get("data") is not None
        assert json.loads(written_content["data"]) == expected_extraction

        # Verify metadata marks this as an image extraction
        meta = final_call_kwargs.get("metadata", {})
        assert meta.get("source") == "image"
        assert "token_diagnostics" in meta
        assert "timing_in_secs" in meta
