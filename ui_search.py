"""Search ("go to passage") overlay mixin for the reader view."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

import verse_ref


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
        self._search_entry.set_placeholder_text("Go to…  e.g. John 3:16, ps 23, Genesis")
        self._search_entry.set_has_frame(False)
        self._search_entry.connect("changed", self._on_search_changed)
        self._search_entry.connect("key-press-event", self._on_search_key)
        row.pack_start(self._search_entry, True, True, 0)
        hint = Gtk.Label(label="↑/↓ select · Enter go · Esc close")
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
        self._search_entry.set_text("")
        self._run_search("")
        self._search_entry.grab_focus()
        self._search_entry.select_region(0, 0)

    def _close_search(self):
        if self._search_overlay is not None:
            self._search_overlay.set_visible(False)
            self._position_bottom_panels()

    def _run_search(self, text):
        for r in self._search_list.get_children():
            self._search_list.remove(r)
        if not self.doc:
            self._search_results = []
            return
        results = verse_ref.search(self.doc.books, text, limit=30)
        self._search_results = results
        for res in results:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(label=res["label"])
            lbl.set_xalign(0.0)
            lbl.set_margin_start(10)
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(4)
            row.add(lbl)
            row._search_data = res
            self._search_list.add(row)
        self._search_list.show_all()
        first = self._search_list.get_row_at_index(0)
        if first is not None:
            self._search_list.select_row(first)

    def _on_search_changed(self, entry):
        self._run_search(entry.get_text())

    def _on_search_key(self, widget, event):
        kv = event.keyval
        if kv == Gdk.KEY_Escape:
            self._close_search()
            self._focus_content()
            return True
        if kv in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._search_select_offset(1 if kv == Gdk.KEY_Down else -1)
            return True
        if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            row = self._search_list.get_selected_row()
            if row is not None:
                self._search_goto(getattr(row, "_search_data", None))
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

    def _on_search_row_activated(self, listbox, row):
        self._search_goto(getattr(row, "_search_data", None))

    def _search_goto(self, data):
        if not data or not self.doc:
            return
        idx = data.get("chapter_index")
        verse = data.get("verse") or 0
        if idx is None:
            return
        self._close_search()
        self._pending_verse = verse
        self._focus = "content"
        self._do_load_chapter(idx)
        self._focus_content()
