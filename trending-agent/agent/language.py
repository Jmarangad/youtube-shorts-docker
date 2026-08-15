"""Title-based language filtering.

Statistical detectors (langdetect/lingua) cannot separate Latin-script
Hinglish from English and are unreliable on very short text, so English
filtering layers several cheap, robust signals:

  1. script check — title must be predominantly Latin script
  2. diacritic check — heavy non-ASCII Latin (Vietnamese/Portuguese/Spanish)
     means the video is not English
  3. Hinglish token blocklist — romanized Hindi reads statistically English
  4. detector — only consulted when the title is long enough to trust

Hindi detection (for the ``non-hindi`` filter) uses three signals:

  1. audio language — ``defaultAudioLanguage == 'hi'`` from the API snippet
  2. script check — Devanagari characters in the title are unambiguous Hindi
  3. Hinglish token blocklist — romanized Hindi titles
"""

from __future__ import annotations

import re
import unicodedata

from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0

_HINGLISH_TOKENS = {
    "aaj", "bhai", "dekho", "dukh", "gya", "hai", "jaan", "jaldi", "juta",
    "karo", "karke", "karne", "ki", "kya", "ladki", "masti", "mera", "mere",
    "meri", "mila", "mummy", "nahi", "pyar", "pyaar", "saaf", "tarika", "wali",
    "zindagi",
}

_SCRIPT_RE = re.compile(r"[a-z']+")
_LATIN = "LATIN"
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_MIN_LATIN_RATIO = 0.98
_MAX_DIACRITIC_RATIO = 0.15
_DETECTOR_MIN_WORDS = 5


def _latin_ratio(title: str) -> float:
    latin = sum(1 for c in title if unicodedata.name(c, "").startswith(_LATIN))
    total = sum(1 for c in title if c.isalpha())
    return latin / total if total else 1.0


def _diacritic_ratio(title: str) -> float:
    latin = sum(1 for c in title if unicodedata.name(c, "").startswith(_LATIN))
    non_ascii = sum(
        1 for c in title
        if ord(c) > 127 and unicodedata.name(c, "").startswith(_LATIN)
    )
    return non_ascii / latin if latin else 0.0


def _tokens(title: str) -> set[str]:
    return set(_SCRIPT_RE.findall(title.lower()))


def _content_words(title: str) -> int:
    return sum(
        1 for w in title.split()
        if not w.startswith("#") and any(c.isalpha() for c in w)
    )


def is_english(title: str) -> bool:
    """Heuristic check that a Short title is (mostly) English."""
    if not title:
        return False
    if _latin_ratio(title) < _MIN_LATIN_RATIO:
        return False
    if _diacritic_ratio(title) > _MAX_DIACRITIC_RATIO:
        return False
    if _tokens(title) & _HINGLISH_TOKENS:
        return False
    if _content_words(title) >= _DETECTOR_MIN_WORDS:
        try:
            return detect(title) == "en"
        except LangDetectException:
            return True
    return True


def is_hindi(title: str, audio_language: str = "") -> bool:
    """Heuristic check that a Short is Hindi.

    Uses the API's ``defaultAudioLanguage`` when available, Devanagari
    script in the title, and the romanized-Hindi (Hinglish) blocklist.
    """
    if audio_language:
        lang = (audio_language or "").lower()
        if lang == "hi" or lang.startswith("hi-"):
            return True
    if not title:
        return False
    if _DEVANAGARI_RE.search(title):
        return True
    if _tokens(title) & _HINGLISH_TOKENS:
        return True
    return False


def matches_language(title: str, language: str, audio_language: str = "") -> bool:
    """True when the title matches the requested language filter.

    ``language`` may be 'all'/'any' (accept everything), 'en' (English),
    'non-hindi' (everything except Hindi), or a 2-letter code.
    """
    if not title:
        return False
    language = (language or "all").strip().lower()
    if language in ("all", "any", ""):
        return True
    if language == "en":
        return is_english(title)
    if language in ("non-hindi", "non_hindi", "not-hindi"):
        return not is_hindi(title, audio_language)
    try:
        return detect(title) == language
    except LangDetectException:
        return True
