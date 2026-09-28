"""Text normalization for product matching (SPEC 4.1).

Thai is written without spaces between words, so spaces typed between Thai
words are dropped, while exactly one space separates Thai text from numbers
and Latin text:

    "ปูนเสือ 50กก." / "ปูนเสือ 50 kg" / "ปูน เสือ ๕๐ กก"  ->  "ปูนเสือ 50 กก"

Unit words are unified only when they follow a number, so ordinary words that
happen to contain e.g. "ม" are left alone.
"""

import re
import unicodedata

THAI = "฀-๿"
THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

# canonical -> spellings (canonical included)
UNIT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "กก": ("กก", "กิโลกรัม", "กิโล", "kg", "kgs"),
    "มม": ("มม", "มิลลิเมตร", "มิล", "mm"),
    "ซม": ("ซม", "เซนติเมตร", "เซน", "cm"),
    "ม": ("ม", "เมตร", "m"),
    "นิ้ว": ("นิ้ว", "inch", "inches"),
    "หุน": ("หุน",),
}
_SPELLING_TO_UNIT = {s: canon for canon, spellings in UNIT_SYNONYMS.items() for s in spellings}


def _unit_alternation() -> str:
    parts = []
    for spelling in sorted(_SPELLING_TO_UNIT, key=len, reverse=True):
        # A unit must not run into more letters of the same script
        # ("5 มัด" must not become "5 ม ัด").
        guard = f"(?![{THAI}])" if re.match(f"[{THAI}]", spelling) else "(?![a-z])"
        parts.append(re.escape(spelling) + guard)
    return "|".join(parts)


_ZERO_WIDTH_RE = re.compile("[​-‍⁠﻿]")
_INCH_MARK_RE = re.compile(r"(\d)\s*[\"”″]")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_STRAY_DOT_RE = re.compile(r"(?<!\d)\.|\.(?!\d)")
_PUNCT_RE = re.compile(r"[,\-–—*_()\[\]{}'\"“”‘’]")
_TIMES_RE = re.compile(r"(?<![a-z])x(?![a-z])")
_NUMBER_UNIT_RE = re.compile(rf"(\d+(?:\.\d+)?)\s*({_unit_alternation()})")
_DIGIT_LETTER_RE = re.compile(rf"(?<=\d)(?=[a-z{THAI}])|(?<=[a-z{THAI}])(?=\d)")
_THAI_LATIN_RE = re.compile(rf"(?<=[{THAI}])(?=[a-z])|(?<=[a-z])(?=[{THAI}])")
_THAI_SPACE_RE = re.compile(rf"(?<=[{THAI}])\s+(?=[{THAI}])")
_SPACES_RE = re.compile(r"\s+")


def _basic(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("ํา", "ำ")  # นิคหิต + สระอา -> สระอำ
    return text.translate(THAI_DIGITS).lower()


def normalize(text: str) -> str:
    text = _basic(text)
    text = _INCH_MARK_RE.sub(r"\1 นิ้ว ", text)
    text = text.replace("×", "x")
    text = _THOUSANDS_RE.sub("", text)
    text = _STRAY_DOT_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _TIMES_RE.sub(" ", text)
    text = _NUMBER_UNIT_RE.sub(lambda m: f"{m.group(1)} {_SPELLING_TO_UNIT[m.group(2)]} ", text)
    text = _DIGIT_LETTER_RE.sub(" ", text)
    text = _THAI_LATIN_RE.sub(" ", text)
    text = _SPACES_RE.sub(" ", text).strip()
    text = _THAI_SPACE_RE.sub("", text)
    return text


def canonical_unit(word: str) -> str:
    """Canonical form of a unit name on its own: 'เมตร' / 'ม.' / 'm' -> 'ม'."""
    text = _basic(word).replace(".", " ")
    text = _SPACES_RE.sub(" ", text).strip()
    text = _THAI_SPACE_RE.sub("", text)
    return _SPELLING_TO_UNIT.get(text, text)
