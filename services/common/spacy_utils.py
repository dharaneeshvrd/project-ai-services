"""
spaCy-backed sentence splitter utility.

Provides a single entry point — ``split_sentences`` — that mirrors the
``SentenceSplitter.split()`` contract: accepts a text string and a two-letter
lowercase language code and returns a list of non-empty sentence strings.


Supported language codes → spaCy model names
---------------------------------------------
en  →  en_core_web_sm
de  →  de_core_news_sm
it  →  it_core_news_sm
fr  →  fr_core_news_sm
ja  →  ja_core_news_sm  (requires sudachipy + sudachidict-core)

Any unrecognised code falls back to the English model.
"""

from __future__ import annotations

import threading
from typing import Dict

import spacy
from spacy.language import Language

from common.misc_utils import get_logger

logger = get_logger("spacy_utils")

# ---------------------------------------------------------------------------
# Language-code → model name mapping
# ---------------------------------------------------------------------------

_LANG_TO_MODEL: Dict[str, str] = {
    "en": "en_core_web_sm",
    "de": "de_core_news_sm",
    "it": "it_core_news_sm",
    "fr": "fr_core_news_sm",
    # Japanese requires sudachipy + sudachidict-core as extra pip dependencies.
    "ja": "ja_core_news_sm",
}

_DEFAULT_LANG = "en"

# ---------------------------------------------------------------------------
# Per-process model cache (thread-safe)
# ---------------------------------------------------------------------------

_cache: Dict[str, Language] = {}
_cache_lock = threading.Lock()


def _load_model(lang: str) -> Language:
    """Load and cache a spaCy model for *lang*, returning the cached copy on repeat calls."""
    with _cache_lock:
        if lang not in _cache:
            model_name = _LANG_TO_MODEL.get(lang, _LANG_TO_MODEL[_DEFAULT_LANG])
            logger.debug(f"Loading spaCy model '{model_name}' for language '{lang}'")
            # Disable components we don't need — only the sentencizer is required.
            nlp = spacy.load(model_name, exclude=["ner", "lemmatizer", "morphologizer"])
            if "sentencizer" not in nlp.pipe_names and "senter" not in nlp.pipe_names and "parser" not in nlp.pipe_names:
                nlp.add_pipe("sentencizer")
            _cache[lang] = nlp
        return _cache[lang]


def split_sentences(text: str, lang: str = _DEFAULT_LANG) -> list[str]:
    """Split *text* into a list of sentence strings using spaCy.

    Drop-in replacement for ``SentenceSplitter(language=lang).split(text)``.

    Args:
        text: Input text to split.
        lang: Lowercase two-letter ISO-639-1 language code (``"en"``, ``"de"``,
              ``"it"``, ``"fr"``, ``"ja"``).  Defaults to ``"en"``.

    Returns:
        List of non-empty sentence strings in document order.
    """
    if not text or not text.strip():
        return []

    resolved = lang if lang in _LANG_TO_MODEL else _DEFAULT_LANG
    nlp = _load_model(resolved)
    doc = nlp(text)
    return [sent.text.strip() for sent in doc.sents if sent.text.strip()]
