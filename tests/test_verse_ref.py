"""Headless tests for verse_ref.search (book/verse reference matching)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import verse_ref  # noqa: E402

# A small fake book tree (no files needed): title + chapters with global index.
BOOKS = [
    {"title": "Genesis", "chapters": [{"title": f"Genesis {i+1}", "index": i} for i in range(50)]},
    {"title": "1 Samuel", "chapters": [{"title": f"1 Samuel {i+1}", "index": 50 + i} for i in range(5)]},
    {"title": "Psalms", "chapters": [{"title": f"Psalms {i+1}", "index": 100 + i} for i in range(150)]},
    {"title": "John", "chapters": [{"title": f"John {i+1}", "index": 300 + i} for i in range(21)]},
    {"title": "1 John", "chapters": [{"title": f"1 John {i+1}", "index": 400 + i} for i in range(5)]},
]


# Fake verse counts per global chapter index (mirrors the UI callback).
VERSE_COUNTS = {0: 25, 300 + 2: 30}


def _verse_count(idx):
    return VERSE_COUNTS.get(idx)


def top(query):
    r = verse_ref.search(BOOKS, query, verse_count=_verse_count)
    return r[0] if r else None


def run():
    # book prefix / exact / abbreviation
    assert top("gen")["chapter_index"] == 0
    assert top("Genesis")["kind"] == "book"
    assert top("gen")["chapters_total"] == 50  # 50 chapters available
    assert top("jn 3:16")["chapter_index"] == 300 + 2
    assert top("jn 3:16")["verse"] == 16
    assert top("jn 3:16")["kind"] == "verse"
    # chapter + verse with different separators
    assert top("Ps 23:1")["verse"] == 1 and top("Ps 23:1")["chapter_index"] == 100 + 22
    assert top("psalm 23 1")["chapter_index"] == 100 + 22  # space-separated verse
    # whole chapter (no verse) is a "chapter" target
    assert top("jn 3")["kind"] == "chapter" and top("jn 3")["verse"] == 0
    assert top("jn 3")["chapter_index"] == 300 + 2
    # verse range
    assert top("gen 1:20-30")["kind"] == "range"
    assert (top("gen 1:20-30")["verse"], top("gen 1:20-30")["verse_end"]) == (20, 30)
    assert top("gen 1:20-30")["label"] == "Genesis 1:20-30"
    # reversed range normalizes lowest-first
    assert (top("gen 1:30-20")["verse"], top("gen 1:30-20")["verse_end"]) == (20, 30)
    # verses_total enrichment via the callback
    got = top("jn 3:16")
    assert got["verses_total"] == 30  # verse_count() returns 30 for John ch 3
    assert top("gen 1:20-30")["verses_total"] == 25
    # numeric book names disambiguate (1 John vs John)
    assert top("1 john")["label"] == "1 John"
    assert top("1 John 2")["chapter_index"] == 400 + 1
    assert top("1 Samuel 3")["chapter_index"] == 50 + 2
    # unknown
    assert top("zzz") is None
    assert top("") is None
    print("ALL VERSE_REF TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run())
