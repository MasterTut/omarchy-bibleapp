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


def top(query):
    r = verse_ref.search(BOOKS, query)
    return r[0] if r else None


def run():
    # book prefix / exact / abbreviation
    assert top("gen")["chapter_index"] == 0
    assert top("Genesis")["kind"] == "book"
    assert top("jn 3:16")["chapter_index"] == 300 + 2
    assert top("jn 3:16")["verse"] == 16
    # chapter + verse with different separators
    assert top("Ps 23:1")["verse"] == 1 and top("Ps 23:1")["chapter_index"] == 100 + 22
    assert top("psalm 23 1")["chapter_index"] == 100 + 22  # space-separated verse
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
