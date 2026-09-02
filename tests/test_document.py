"""Headless tests for Document (reading state + navigation + note keys).

    .venv/bin/python tests/test_document.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from document import Document  # noqa: E402


class FakeSource:
    """Minimal stand-in for EpubSource (no file access)."""
    def __init__(self, chapters, books):
        self.chapters = chapters   # list of (book, title, path, name)
        self.books = books

    def close(self):
        pass


def make_doc(n_chapters=5, book="Genesis"):
    chapters = [(book, f"{book} {i+1}", f"/tmp/{i}.html", f"{i}.html") for i in range(n_chapters)]
    books = [{"title": book, "chapters": [{"title": c[1], "index": i} for i, c in enumerate(chapters)]}]
    return Document(FakeSource(chapters, books), "/books/KJV.epub")


def run():
    d = make_doc(5)

    # navigation
    assert d.chapter_count() == 5
    assert d.next_index() == 1
    d.chapter_index = 4
    assert d.next_index() is None, "should not advance past last chapter"
    assert d.prev_index() == 3
    d.chapter_index = 0
    assert d.prev_index() is None, "should not go before first chapter"
    assert d.jumpable(2) and not d.jumpable(9)

    # position updates
    d.on_ready(pages=8, char_offset=100)
    assert (d.page, d.pages, d.char_offset, d.verse) == (0, 8, 100, 0)
    d.on_page(cur=3, pages=8, char_offset=450)
    assert (d.page, d.char_offset) == (3, 450)
    d.on_verse(7)
    assert d.verse == 7

    # note keying uses char offset (stable) + legacy uses page
    assert d.note_key() == "KJV.epub|0|450"
    assert d.legacy_note_keys() == ["KJV.epub|0|3"]

    # persistence round-trips as 1-based
    assert d.to_state() == {"book": "KJV.epub", "chapter": 1, "page": 4}

    # TOC locate
    assert d.locate() == (0, 0)
    d.chapter_index = 2
    assert d.locate() == (0, 2)

    # titles
    assert d.chapter_title() == "Genesis 3"
    assert d.chapter_book() == "Genesis"

    print("Document unit tests passed")

    # Integration: open a real bundled book if present.
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kjv = os.path.join(base, "translations", "KJV.epub")
    if os.path.isfile(kjv):
        doc = Document.open(kjv)
        try:
            assert doc is not None and doc.chapter_count() > 1000
            assert doc.note_key().startswith("KJV.epub|")
            assert doc.locate() == (0, 0)
            print("Document integration (KJV) passed:", doc.chapter_count(), "chapters")
        finally:
            doc.close()
    else:
        print("Document integration skipped (no KJV.epub)")

    print("\nALL DOCUMENT TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run())
