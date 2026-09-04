"""
Sentence splitting utility.

Provides a single entry point — ``split_sentences`` — that accepts a text
string and a two-letter lowercase language code and returns a list of
non-empty sentence strings.

Routing logic
-------------
* Japanese (``"ja"``) — uses spaCy ``ja_core_news_sm`` (requires sudachipy
  and sudachidict-core).
* All other languages — uses ``sentence-splitter`` (lightweight, rule-based),
  falling back to ``"en"`` for unrecognised codes.
"""

from __future__ import annotations

import threading
from typing import Dict

import spacy
from sentence_splitter import SentenceSplitter
from spacy.language import Language

from common.misc_utils import get_logger

logger = get_logger("spacy_utils")

# ---------------------------------------------------------------------------
# spaCy — Japanese only
# ---------------------------------------------------------------------------

_JA_MODEL = "ja_core_news_sm"
_JA_LANG = "ja"

_cache: Dict[str, Language] = {}
_cache_lock = threading.Lock()


def _load_ja_model() -> Language:
    """Load and cache the Japanese spaCy model."""
    with _cache_lock:
        if _JA_LANG not in _cache:
            logger.debug(f"Loading spaCy model '{_JA_MODEL}' for language 'ja'")
            nlp = spacy.load(_JA_MODEL, exclude=["ner", "lemmatizer", "morphologizer"])
            if "sentencizer" not in nlp.pipe_names and "senter" not in nlp.pipe_names and "parser" not in nlp.pipe_names:
                nlp.add_pipe("sentencizer")
            _cache[_JA_LANG] = nlp
        return _cache[_JA_LANG]


def _split_with_spacy_ja(text: str) -> list[str]:
    """Split Japanese text into sentences using spaCy."""
    nlp = _load_ja_model()
    doc = nlp(text)
    return [sent.text.strip() for sent in doc.sents if sent.text.strip()]


# ---------------------------------------------------------------------------
# sentence-splitter — all non-Japanese languages
# ---------------------------------------------------------------------------

_SENTENCE_SPLITTER_LANGS = {"en", "de", "it", "fr"}
_DEFAULT_LANG = "en"

_splitter_cache: Dict[str, SentenceSplitter] = {}
_splitter_cache_lock = threading.Lock()


def _get_splitter(lang: str) -> SentenceSplitter:
    """Return a cached ``SentenceSplitter`` instance for *lang*."""
    resolved = lang if lang in _SENTENCE_SPLITTER_LANGS else _DEFAULT_LANG
    with _splitter_cache_lock:
        if resolved not in _splitter_cache:
            logger.debug(f"Creating SentenceSplitter for language '{resolved}'")
            _splitter_cache[resolved] = SentenceSplitter(language=resolved)
        return _splitter_cache[resolved]


def _split_with_sentence_splitter(text: str, lang: str) -> list[str]:
    """Split text into sentences using sentence-splitter."""
    splitter = _get_splitter(lang)
    return [s.strip() for s in splitter.split(text) if s.strip()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def split_sentences(text: str, lang: str = _DEFAULT_LANG) -> list[str]:
    """Split *text* into a list of sentence strings.

    Uses spaCy for Japanese (``"ja"``); uses ``sentence-splitter`` for all
    other languages, falling back to ``"en"`` for unrecognised codes.

    Args:
        text: Input text to split.
        lang: Lowercase two-letter ISO-639-1 language code.  Defaults to
              ``"en"``.

    Returns:
        List of non-empty sentence strings in document order.
    """
    if not text or not text.strip():
        return []

    if lang == _JA_LANG:
        return _split_with_spacy_ja(text)

    return _split_with_sentence_splitter(text, lang)
