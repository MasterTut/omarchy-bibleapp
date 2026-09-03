"""TOC overlay (books -> chapters -> verses) mixin for the reader view."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib


class TocMixin:
    def _build_toc_overlay(self):
        """Build the chapter-list overlay ("Table of Contents")."""
        self.toc_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toc_overlay.set_visible(False)
        self.toc_overlay.set_halign(Gtk.Align.FILL)
        self.toc_overlay.set_valign(Gtk.Align.FILL)
        self.toc_overlay.get_style_context().add_class("toc-overlay")

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        bar.set_margin_top(12)
        bar.set_margin_bottom(8)

        back_btn = Gtk.Button(label="Back")
        back_btn.get_style_context().add_class("toc-back")
        back_btn.connect("clicked", self._on_toc_back)
        self.toc_back_btn = back_btn
        bar.pack_start(back_btn, False, False, 0)

        label = Gtk.Label(label="Books")
        label.get_style_context().add_class("title-label")
        label.set_halign(Gtk.Align.START)
        self.toc_title_label = label
        bar.pack_start(label, True, True, 0)

        count = Gtk.Label(label="")
        count.get_style_context().add_class("progress-label")
        self.toc_count = count
        bar.pack_end(count, False, False, 0)

        row = Gtk.ListBox()
        row.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.toc_list = row
        self.toc_list.set_vexpand(True)
        self.toc_list.set_margin_start(16)
        self.toc_list.set_margin_end(16)
        self.toc_list.set_margin_bottom(16)
        self.toc_list.connect("row-activated", self._on_toc_row_activated)

        self.toc_box = self.toc_list

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.add(self.toc_list)
        self._toc_scroller = scroller

        self.toc_overlay.pack_start(bar, False, False, 0)
        self.toc_overlay.pack_start(scroller, True, True, 0)

    def _refresh_toc(self):
        """(Re)populate the TOC list based on the current mode."""
        for r in self.toc_list.get_children():
            self.toc_list.remove(r)
        self._toc_row_index = {}
        self._toc_rows = []

        if not self._toc_books:
            empty = Gtk.Label(label="No table of contents available.")
            empty.get_style_context().add_class("progress-label")
            self.toc_list.add(empty)
            self.toc_title_label.set_text("Contents")
            self.toc_count.set_text("")
            self.toc_back_btn.set_visible(False)
            return

        items = []
        title = "Books"
        if self._toc_mode == "books":
            title = "Books"
            items = [
                (b["title"], {"type": "book", "index": i})
                for i, b in enumerate(self._toc_books)
            ]
        elif self._toc_mode == "chapters":
            book = self._toc_books[self._toc_book_idx]
            title = book["title"]
            items = [
                (
                    c["title"],
                    {
                        "type": "chapter",
                        "index": c["index"],
                        "book_idx": self._toc_book_idx,
                        "chapter_in_book_idx": ci,
                    },
                )
                for ci, c in enumerate(book["chapters"])
            ]
        elif self._toc_mode == "verses":
            book = self._toc_books[self._toc_book_idx]
            chapter = book["chapters"][self._toc_chapter_idx]
            title = f"{book['title']} · {chapter['title']}"
            verse_list = self.source.verses(chapter["index"]) if self.source else []
            items = [
                (
                    f"Verse {vn}",
                    {
                        "type": "verse",
                        "verse": vn,
                        "chapter_index": chapter["index"],
                    },
                )
                for vn in verse_list
            ]

        self.toc_title_label.set_text(title)
        self.toc_back_btn.set_visible(self._toc_mode != "books")
        self.toc_count.set_text(
            f"{len(items)} item{'s' if len(items) != 1 else ''}"
        )

        for i, (label, data) in enumerate(items):
            row = Gtk.ListBoxRow()
            self._toc_row_index[row] = i
            self._toc_rows.append(row)
            row._toc_data = data
            txt = Gtk.Label(label=label)
            txt.set_xalign(0.0)
            txt.set_line_wrap(True)
            txt.set_halign(Gtk.Align.START)
            txt.get_style_context().add_class("toc-title")
            if self._toc_mode == "chapters" and data.get("index") == self.chapter_index:
                txt.get_style_context().add_class("toc-current")
            row.add(txt)
            self.toc_list.add(row)

        self.toc_list.show_all()

    def _toggle_toc(self):
        if self.toc_overlay.get_visible():
            self._hide_toc()
        else:
            self._show_toc()

    def _toc_locate_current_chapter(self):
        """Return (book_idx, chapter_in_book_idx) for the current chapter."""
        return self.doc.locate() if self.doc else (0, 0)

    def _show_toc(self):
        if not self.chapters or not self._toc_books:
            return
        self._hide_notes()
        self._hide_settings()
        self._hide_help()
        self._toc_mode = "books"
        self._toc_book_idx, self._toc_chapter_idx = self._toc_locate_current_chapter()
        self._toc_verse_idx = 0
        self._refresh_toc()
        self.toc_overlay.show_all()
        self.toc_overlay.set_visible(True)
        # Highlight the book containing the current chapter.
        if self._toc_rows and 0 <= self._toc_book_idx < len(self._toc_rows):
            row = self._toc_rows[self._toc_book_idx]
            self.toc_list.select_row(row)
            self.toc_list.grab_focus()
            self._scroll_to_toc_row(row)

    def _hide_toc(self):
        self.toc_overlay.set_visible(False)
        if self._focus not in ("notes", "refs"):
            self._focus = "content"
        if self.webview and not self._on_home:
            self.webview.grab_focus()
        self._update_header_focus()

    def _toc_selected_index(self):
        row = self.toc_list.get_selected_row()
        if row is not None:
            return self._toc_row_index.get(row)
        if self._toc_rows:
            return 0
        return None

    def _toc_move(self, offset):
        current = self._toc_selected_index()
        if current is None or not self._toc_rows:
            return
        target = current + offset
        if target < 0 or target >= len(self._toc_rows):
            return
        row = self._toc_rows[target]
        self.toc_list.select_row(row)
        self._scroll_to_toc_row(row)

    def _scroll_to_toc_row(self, row):
        """Scroll the TOC list so the selected row is fully visible.

        scroll_to_row can under-scroll at the very bottom (the last row can end
        up below the fold), so we scroll minimally via the ScrolledWindow
        adjustment instead, once the row has been allocated.
        """
        def ensure():
            if row is None or self._toc_scroller is None:
                return False
            adj = self._toc_scroller.get_vadjustment()
            if adj is None:
                self.toc_list.scroll_to_row(row)
                return False
            # Translate the row's allocation into the scrolled window's coords.
            alloc = row.get_allocation()
            list_alloc = self.toc_list.get_allocation()
            top = alloc.y - (list_alloc.y - 0)
            bottom = top + alloc.height
            vis_top = adj.get_value()
            vis_bottom = vis_top + adj.get_page_size()
            if top < vis_top:
                adj.set_value(max(adj.get_lower(), top))
            elif bottom > vis_bottom:
                adj.set_value(min(adj.get_upper() - adj.get_page_size(),
                                  bottom - adj.get_page_size()))
            return False

        # Defer one frame so the newly selected row has an allocation.
        GLib.idle_add(ensure)

    def _toc_activate_current(self):
        if not self.toc_overlay.get_visible():
            return
        row = self.toc_list.get_selected_row()
        if row is not None:
            self._on_toc_row_activated(self.toc_list, row)

    def _on_toc_row_activated(self, listbox, row):
        data = getattr(row, "_toc_data", None)
        if not data:
            return
        dtype = data.get("type")
        if dtype == "book":
            self._toc_mode = "chapters"
            self._toc_book_idx = data.get("index", 0)
            self._toc_chapter_idx = 0
            self._refresh_toc()
            if self._toc_rows:
                self.toc_list.select_row(self._toc_rows[0])
                self.toc_list.grab_focus()
            return
        if dtype == "chapter":
            chapter_index = data.get("index", 0)
            self._toc_chapter_idx = data.get("chapter_in_book_idx", 0)
            self._do_load_chapter(chapter_index)
            self._toc_mode = "verses"
            self._refresh_toc()
            if self._toc_rows:
                self.toc_list.select_row(self._toc_rows[0])
                self.toc_list.grab_focus()
            return
        if dtype == "verse":
            vn = data.get("verse", 0)
            self._run_js(f"goToVerse({vn});")
            self._hide_toc()
            return

    def _on_toc_back(self, *args):
        if not self.toc_overlay.get_visible():
            return
        if self._toc_mode == "verses":
            self._toc_mode = "chapters"
            self._refresh_toc()
            if self._toc_rows and 0 <= self._toc_chapter_idx < len(self._toc_rows):
                self.toc_list.select_row(self._toc_rows[self._toc_chapter_idx])
                self.toc_list.grab_focus()
            return
        if self._toc_mode == "chapters":
            self._toc_mode = "books"
            self._refresh_toc()
            if self._toc_rows and 0 <= self._toc_book_idx < len(self._toc_rows):
                self.toc_list.select_row(self._toc_rows[self._toc_book_idx])
                self.toc_list.grab_focus()
            return
        self._hide_toc()
