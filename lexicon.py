"""Offline interlinear + Strong's lexicon (GTK-free, testable).

Data lives in JSON under a data dir (repo ``data/lexicon`` by default, or
``~/.config/omarchy-bible/lexicon`` if present). Expected files:

- nt.json  / ot.json : {"<book>:<chapter>:<verse>": [word, ...]}
  where word = {"w","lemma","translit","pos","strongs"} (strongs like "G1722"
  or "H7225").
- strongs.json : {"G1722": {"gloss": "...", "def": "..."}, "H7225": {...}}

``interlinear(book_index, chapter, verse)`` returns the word list (book_index is
1..66 in canonical English-book order). Drop the full MorphGNT / Biblearc /
Strong's datasets into the same schema later and it just works.
"""
import json
import os
import re

# Canonical English-book order (1..66) used to key the datasets.
BOOKS = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua",
    "Judges", "Ruth", "1 Samuel", "2 Samuel", "1 Kings", "2 Kings",
    "1 Chronicles", "2 Chronicles", "Ezra", "Nehemiah", "Esther", "Job",
    "Psalms", "Proverbs", "Ecclesiastes", "Song of Solomon", "Isaiah",
    "Jeremiah", "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel",
    "Amos", "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk", "Zephaniah",
    "Haggai", "Zechariah", "Malachi", "Matthew", "Mark", "Luke", "John",
    "Acts", "Romans", "1 Corinthians", "2 Corinthians", "Galatians",
    "Ephesians", "Philippians", "Colossians", "1 Thessalonians",
    "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus", "Philemon",
    "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John", "3 John",
    "Jude", "Revelation",
]
_BOOK_INDEX = {b: i + 1 for i, b in enumerate(BOOKS)}

_IS_TEST = "PYTEST_CURRENT_TEST" in os.environ
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_USER_DIR = os.path.expanduser("~/.config/omarchy-bible/lexicon")
_REPO_DIR = os.path.join(_PKG_DIR, "data", "lexicon")

# Where the sample data ships for now (a handful of real verses).
_SAMPLE_DIR = os.path.join(_PKG_DIR, "data", "lexicon", "sample")


def _dir():
    for d in (_USER_DIR, _REPO_DIR):
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "nt.json")):
            return d
    return _REPO_DIR if os.path.isdir(_REPO_DIR) else _SAMPLE_DIR


def _load(name):
    d = _dir()
    for base in (d, _SAMPLE_DIR):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                return {}
    return {}


_NT = None
_OT = None
_STRONGS = None


def _ensure():
    global _NT, _OT, _STRONGS
    if _NT is None:
        _NT = _load("nt.json")
        _OT = _load("ot.json")
        _STRONGS = _load("strongs.json")


_SIMPLE_SINGLETONS = {"psalm": "Psalms", "the psalms": "Psalms",
                      "psalms": "Psalms"}
_ROMAN_PLURALS = {
    "corinthians": "Corinthians", "thessalonians": "Thessalonians",
    "timothy": "Timothy", "john": "John", "peter": "Peter",
    "kings": "Kings", "chronicles": "Chronicles", "samuel": "Samuel",
}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
          "viii": 8}


def _normalize_book(token):
    """Return a canonical book name from a fuzzy title, or None."""
    if not token:
        return None
    t = re.sub(r"\s+", " ", token.strip()).lower().rstrip(".,;:()")
    # "The ..." / "The Book of ..." / "Book of ..."
    t = re.sub(r"^(the\s+)?(book\s+of\s+)?", "", t)
    if t in _SIMPLE_SINGLETONS:
        return _SIMPLE_SINGLETONS[t]
    if t in ("song", "song of songs"):
        return "Song of Solomon"
    words = t.split()
    # Roman-numeral ordinal prefix, e.g. "ii corinthians" -> "2 Corinthians"
    if words and words[0].rstrip(".") in _ROMAN:
        num = _ROMAN[words[0].rstrip(".")]
        rest = " ".join(words[1:])
        if rest in _ROMAN_PLURALS:
            return "%d %s" % (num, _ROMAN_PLURALS[rest])
    books_l = {b.lower(): b for b in BOOKS}
    # longest-first so "song of solomon" wins over "song"
    for bk in sorted(books_l, key=len, reverse=True):
        if t.endswith(bk):
            return books_l[bk]
    return None


def book_number(display_name):
    """Canonical 1..66 index for an English book display name, or None."""
    if not display_name:
        return None
    canon = _normalize_book(display_name)
    if canon:
        return _BOOK_INDEX.get(canon)
    return None


def is_ot(book_index):
    return book_index is not None and 1 <= book_index <= 39


def interlinear(book_index, chapter, verse):
    """Return the list of original-language words for a verse ([] if unknown)."""
    _ensure()
    if not book_index:
        return []
    key = "%d:%d:%d" % (book_index, chapter, verse)
    data = _OT if is_ot(book_index) else _NT
    return data.get(key, [])


def has_data(book_index, chapter, verse):
    return bool(interlinear(book_index, chapter, verse))


def strongs(code):
    """Return the Strong's entry for a code like 'G1722'/'H7225', or None."""
    _ensure()
    if not code:
        return None
    return (_STRONGS or {}).get(code.upper())


def reload():
    global _NT, _OT, _STRONGS
    _NT = _OT = _STRONGS = None
