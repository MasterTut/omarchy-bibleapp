"""Search ("go to passage") overlay mixin for the reader view."""
import html
import re

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

import verse_ref

_TAG_RE = re.compile(r"<[^>]+>")
_DATA_VN_RE = re.compile(r'data-vn="(\d+)"')
# Both marker flavours share a data-vn attribute: one-verse paragraphs
# (<sup class="v" data-vn="1">1</sup>…) and multi-verse spans
# (<span class="v" data-vn="1">1 </span><span …>…</span> inside one <p>).
_MARK_TAG_RE = re.compile(
    r"<(?:sup|span)\b[^>]*data-vn=\"(\d+)\"[^>]*>.*?</(?:sup|span)>",
    re.S,
)
_BLOCK_END_RE = re.compile(r"</(?:p|div|h\d)>")


def _extract_verses(body):
    """{verse number: plain text} from annotated chapter HTML."""
    body2 = _MARK_TAG_RE.sub(lambda m: "\x01%d\x02" % int(m.group(1)), body)
    parts = re.split(r"\x01(\d+)\x02", body2)
    verses = {}
    for i in range(1, len(parts), 2):
        vn = int(parts[i])
        raw = _BLOCK_END_RE.split(parts[i + 1], maxsplit=1)[0]
        txt = _TAG_RE.sub("", raw)
        txt = html.unescape(txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        verses[vn] = txt
    return verses


class SearchMixin:
    # ---------------- Search ("Go to passage") ----------------
    def _build_search_overlay(self):
        # A bottom bar: the input sits at the very bottom and the suggestion
        # list grows upward above it.
        self._search_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._search_overlay.set_visible(False)
        self._search_overlay.set_halign(Gtk.Align.FILL)
        self._search_overlay.set_valign(Gtk.Align.END)
        self._search_overlay.get_style_context().add_class("search-overlay")
        # Track the bar's real (content-hugging) height so panels above it stack.
        self._search_overlay.connect("size-allocate", self._on_search_alloc)

        self._search_list = Gtk.ListBox()
        self._search_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._search_list.connect("row-activated", self._on_search_row_activated)
        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Hug the content: grow with the number of results, up to a cap, then scroll.
        list_scroll.set_propagate_natural_height(True)
        list_scroll.set_max_content_height(300)
        list_scroll.add(self._search_list)
        self._search_overlay.pack_start(list_scroll, False, False, 0)

        # Verse preview panel: shown when Enter is pressed on a result. It sits
        # between the result list and the input row, and is dismissed with q,
        # Esc, or the ✕ button.
        self._search_preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._search_preview.set_visible(False)
        self._search_preview.get_style_context().add_class("search-preview")
        self._search_preview.set_can_focus(True)

        prev_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._preview_ref = Gtk.Label(label="")
        self._preview_ref.set_xalign(0.0)
        self._preview_ref.set_halign(Gtk.Align.START)
        self._preview_ref.get_style_context().add_class("preview-ref")
        prev_head.pack_start(self._preview_ref, True, True, 0)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", lambda *_: self._search_open(self._preview_data))
        prev_head.pack_start(open_btn, False, False, 0)

        close_btn = Gtk.Button(label="✕")
        close_btn.connect("clicked", lambda *_: self._close_search_preview())
        prev_head.pack_start(close_btn, False, False, 0)

        self._search_preview.pack_start(prev_head, False, False, 0)

        self._preview_text = Gtk.Label(label="")
        self._preview_text.set_xalign(0.0)
        self._preview_text.set_halign(Gtk.Align.START)
        self._preview_text.set_line_wrap(True)
        self._preview_text.get_style_context().add_class("preview-text")
        self._search_preview.pack_start(self._preview_text, False, False, 0)

        self._search_overlay.pack_start(self._search_preview, False, False, 0)

        # Input row (bottom of the bar).
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_start(16)
        row.set_margin_end(16)
        row.set_margin_top(6)
        row.set_margin_bottom(8)
        prefix = Gtk.Label(label="⌕")
        prefix.get_style_context().add_class("title-label")
        row.pack_start(prefix, False, False, 0)
        self._search_entry = Gtk.Entry()
        self._search_entry.set_placeholder_text(
            "Go to…  e.g. John 3:16, gen 1:20-30, Genesis 1"
        )
        self._search_entry.set_has_frame(False)
        self._search_entry.connect("changed", self._on_search_changed)
        self._search_entry.connect("key-press-event", self._on_search_key)
        row.pack_start(self._search_entry, True, True, 0)
        hint = Gtk.Label(label="↑/↓ select · Enter preview · q/Esc close")
        hint.get_style_context().add_class("progress-label")
        row.pack_start(hint, False, False, 0)
        self._search_overlay.pack_start(row, False, False, 0)

    def _open_search(self):
        if not self.book_path or not self.chapters:
            return
        self._hide_settings()
        self._hide_help()
        self._search_overlay.show_all()
        self._search_overlay.set_visible(True)
        self._position_bottom_panels()
        self._preview_data = None
        self._close_search_preview()
        self._search_entry.set_text("")
        self._run_search("")
        self._search_entry.grab_focus()
        self._search_entry.select_region(0, 0)

    def _close_search(self):
        if self._search_overlay is not None:
            self._search_overlay.set_visible(False)
            self._preview_data = None
            self._search_preview.set_visible(False)
            self._position_bottom_panels()

    def _search_preview_is_open(self):
        return self._search_preview is not None and self._search_preview.get_visible()

    def _close_search_preview(self):
        if self._search_preview is not None:
            self._search_preview.set_visible(False)
            self._preview_data = None

    def _count_chapter_verses(self, chapter_index):
        """Number of verses in a chapter, for the auto-complete hint (or None)."""
        try:
            body = self.source.chapter_html(chapter_index)
        except Exception:
            return None
        if not body:
            return None
        vns = {int(v) for v in _DATA_VN_RE.findall(body)}
        return len(vns) or None

    def _search_subtitle(self, res):
        """Availability hint under a result: chapters/verses in scope."""
        kind = res.get("kind")
        total = res.get("verses_total")
        if kind == "book":
            return f"{res.get('chapters_total', '?')} chapters"
        if kind == "chapter":
            return f"{total} verses" if total else "whole chapter"
        v = res.get("verse") or 0
        end = res.get("verse_end")
        if kind == "range" and end is not None:
            span = f"{v}\u2013{end}"
            return f"{span} of {total} verses" if total else f"verses {span}"
        return f"verse {v}" + (f" of {total} verses" if total else "")

    def _run_search(self, text):
        for r in self._search_list.get_children():
            self._search_list.remove(r)
        if not self.doc:
            self._search_results = []
            return
        results = verse_ref.search(
            self.doc.books, text, limit=30,
            verse_count=self._count_chapter_verses,
        )
        self._search_results = results
        for res in results:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.set_margin_start(10)
            box.set_margin_top(4)
            box.set_margin_bottom(4)
            title = Gtk.Label(label=res["label"])
            title.set_xalign(0.0)
            title.set_halign(Gtk.Align.START)
            box.pack_start(title, False, False, 0)
            sub = self._search_subtitle(res)
            if sub:
                st = Gtk.Label(label=sub)
                st.set_xalign(0.0)
                st.set_halign(Gtk.Align.START)
                st.get_style_context().add_class("progress-label")
                st.set_margin_top(1)
                box.pack_start(st, False, False, 0)
            row.add(box)
            row._search_data = res
            self._search_list.add(row)
        self._search_list.show_all()
        first = self._search_list.get_row_at_index(0)
        if first is not None:
            self._search_list.select_row(first)

    def _on_search_changed(self, entry):
        # Editing the query invalidates a shown verse preview.
        self._close_search_preview()
        self._run_search(entry.get_text())

    def _on_search_key(self, widget, event):
        kv = event.keyval
        if kv == Gdk.KEY_Escape:
            if self._search_preview_is_open():
                self._close_search_preview()
                self._search_entry.grab_focus()
            else:
                self._close_search()
                self._focus_content()
            return True
        if self._search_preview_is_open() and kv in (Gdk.KEY_q, Gdk.KEY_Q):
            self._close_search_preview()
            self._search_entry.grab_focus()
            return True
        if kv in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._search_select_offset(1 if kv == Gdk.KEY_Down else -1)
            return True
        if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            row = self._search_list.get_selected_row()
            if row is not None:
                data = getattr(row, "_search_data", None)
                if self._search_preview_is_open():
                    self._search_open(data)
                else:
                    self._show_search_preview(data)
            return True
        return False

    def _search_select_offset(self, delta):
        count = len(self._search_results)
        if count == 0:
            return
        cur = self._search_list.get_selected_row()
        idx = self._search_list.get_row_index(cur) if cur else 0
        idx = max(0, min(count - 1, idx + delta))
        row = self._search_list.get_row_at_index(idx)
        if row is not None:
            self._search_list.select_row(row)
            self._search_list.scroll_to(row)
            if self._search_preview_is_open():
                self._show_search_preview(getattr(row, "_search_data", None))

    def _on_search_row_activated(self, listbox, row):
        # Double-click (or Enter on the focused list) opens the passage.
        self._search_open(getattr(row, "_search_data", None))

    def _show_search_preview(self, data):
        """Bottom panel previewing the selected passage without navigating.

        Handles single verses, whole chapters (``Genesis 1``) and ranges
        (``Genesis 1:20-30``).
        """
        if not data or not getattr(self, "doc", None):
            return
        idx = data.get("chapter_index")
        if idx is None:
            return
        start = int(data.get("verse") or 0)
        end = data.get("verse_end")
        end = int(end) if end else None
        verses = {}
        try:
            verses = _extract_verses(self.source.chapter_html(idx))
        except Exception:
            verses = {}
        if verses:
            vns = sorted(verses)
            lo = start if start else vns[0]
            hi = end if end is not None else (vns[-1] if not start else lo)
            text = " ".join(verses[v] for v in vns if lo <= v <= hi)
            if not text:
                text = "(no verse text)"
        else:
            text = "(no verse text)"
        self._preview_data = data
        self._preview_ref.set_text(data.get("label") or "")
        self._preview_text.set_text(text)
        self._search_preview.show_all()
        self._search_preview.set_visible(True)
        self._search_entry.grab_focus()

    def _search_open(self, data):
        if not data or not self.doc:
            return
        idx = data.get("chapter_index")
        verse = data.get("verse") or 0
        if idx is None:
            return
        # Remember where we were so Backspace can return after the jump.
        path = getattr(self, "book_path", None)
        cur = getattr(self, "chapter_index", None)
        if path and cur is not None:
            self._prev_search_pos = (path, cur, self._current_verse or 0)
        self._close_search()
        self._pending_verse = verse
        self._focus = "content"
        self._do_load_chapter(idx)
        self._focus_content()
