"""Document: the open-book model shared by the whole UI.

Wraps an :class:`EpubSource` with the *reading state* — current chapter, page,
character offset, total pages and highlighted verse — plus the navigation
predicates and note-keying that depend on that state. This is the single
source of truth for "where am I in the book"; the GTK layer observes it (via
thin properties on ``OmarchyReader``) so nothing else has to track it.

Like EpubSource it is GTK-free and can be exercised in headless tests.
"""
import os

from epubsource import EpubSource


class Document:
    def __init__(self, source, path):
        self.source = source
        self.path = path
        self.chapter_index = 0
        self.page = 0
        self.char_offset = 0
        self.pages = 0
        self.verse = 0

    # ---------------- lifecycle ----------------
    @classmethod
    def open(cls, path):
        """Load an EPUB into a Document, or return None if it has no chapters."""
        source = EpubSource(path)
        if not source.load():
            source.close()
            return None
        return cls(source, path)

    def close(self):
        if self.source is not None:
            self.source.close()

    # ---------------- model helpers ----------------
    @property
    def book_name(self):
        return os.path.basename(self.path) if self.path else ""

    @property
    def chapters(self):
        return self.source.chapters if self.source else []

    @property
    def books(self):
        return self.source.books if self.source else []

    def chapter_count(self):
        return len(self.chapters)

    def _chapter_tuple(self, index=None):
        i = self.chapter_index if index is None else index
        if 0 <= i < len(self.chapters):
            return self.chapters[i]
        return None

    def chapter_title(self, index=None):
        t = self._chapter_tuple(index)
        return t[1] if t else ""

    def chapter_book(self, index=None):
        t = self._chapter_tuple(index)
        return t[0] if t else ""

    # ---------------- navigation (pure) ----------------
    def next_index(self):
        return self.chapter_index + 1 if self.chapter_index + 1 < self.chapter_count() else None

    def prev_index(self):
        return self.chapter_index - 1 if self.chapter_index - 1 >= 0 else None

    def jumpable(self, index):
        return 0 <= index < self.chapter_count()

    # ---------------- position updates ----------------
    def on_ready(self, pages, char_offset):
        """Chapter finished loading and paginating."""
        self.page = 0
        self.pages = pages
        self.char_offset = char_offset
        self.verse = 0

    def on_page(self, cur, pages, char_offset):
        self.page = cur
        self.pages = pages
        self.char_offset = char_offset

    def on_verse(self, verse):
        self.verse = verse

    # ---------------- note keying ----------------
    def note_key(self):
        """Stable location key for the current page (anchored by char offset)."""
        if not self.chapters:
            return None
        return f"{self.book_name}|{self.chapter_index}|{self.char_offset}"

    def legacy_note_keys(self):
        """Older notes keyed by page number instead of char offset."""
        if not self.chapters:
            return []
        return [f"{self.book_name}|{self.chapter_index}|{self.page}"]

    def location_label(self, display_name):
        return f"{display_name} · ch {self.chapter_index + 1} · page {self.page + 1}"

    # ---------------- persistence ----------------
    def to_state(self):
        return {
            "book": self.book_name,
            "chapter": self.chapter_index + 1,
            "page": self.page + 1,
        }

    # ---------------- TOC lookup ----------------
    def locate(self, chapter_index=None):
        """Return (book_index, chapter_index_in_book) for a global chapter."""
        i = self.chapter_index if chapter_index is None else chapter_index
        for bi, book in enumerate(self.books):
            for ci, ch in enumerate(book["chapters"]):
                if ch["index"] == i:
                    return bi, ci
        return 0, 0
