"""Headless tests for EpubSource (no GTK / display required).

Run directly with the project venv:

    .venv/bin/python tests/test_epubsource.py

Or under pytest if installed:

    .venv/bin/pytest tests/test_epubsource.py -q

Sample EPUBs are looked up in ../translations and, optionally, via the
OMARCHY_BIBLE_STUDY env var (path to an ESV Study Bible EPUB). Missing files are
skipped rather than failed, so the suite stays green on a checkout that ships
only the bundled books.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from epubsource import EpubSource  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSLATIONS = os.path.join(BASE, "translations")


def _find(name):
    p = os.path.join(TRANSLATIONS, name)
    return p if os.path.isfile(p) else None


# (label, path, min_chapters, min_books, first_scripture_index)
CASES = [
    ("KJV", _find("KJV.epub"), 1100, 60, 0),
    ("ASV", _find("ASV.epub"), 1100, 60, 0),
    ("Crossway ESV", _find("The Holy Bible English Standard Version (E - Crossway Bibles.epub"),
     1000, 60, 1),  # chapter 0 is the Preface; scripture starts at 1
    ("ESV Study Bible", os.environ.get("OMARCHY_BIBLE_STUDY"), 1100, 60, 0),
]


def run():
    passed = skipped = 0
    for label, path, min_ch, min_books, first in CASES:
        if not path or not os.path.isfile(path):
            print(f"SKIP {label}: file not found")
            skipped += 1
            continue
        src = EpubSource(path)
        try:
            assert src.load(), f"{label}: load() returned False"
            assert len(src.chapters) >= min_ch, (
                f"{label}: {len(src.chapters)} chapters < {min_ch}"
            )
            assert len(src.books) >= min_books, (
                f"{label}: {len(src.books)} books < {min_books}"
            )
            verses = src.verses(first)
            assert len(verses) >= 3, f"{label}: too few verses in first chapter"
            assert 1 in verses, f"{label}: verse 1 not detected"
            html_body = src.chapter_html(first)
            assert 'class="v"' in html_body, f"{label}: no tagged verses in HTML"
            assert "data-vn=" in html_body, f"{label}: no verse markers in HTML"
            # resources/refs must not raise even when empty
            src.refs(first)
            src.resources(first)
            print(f"PASS {label}: {len(src.chapters)} chapters, "
                  f"{len(src.books)} books, first-chapter verses={len(verses)}")
            passed += 1
        finally:
            src.close()
    print(f"\n{passed} passed, {skipped} skipped")
    assert passed >= 1, "no sample EPUBs found to test"
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(run())
