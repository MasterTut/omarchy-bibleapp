#!/usr/bin/env python3
"""Omarchy-Bible - a minimal EPUB Bible reader styled after the Omarchy Ash theme."""

import html
import os
import re
import sys
import shlex
import threading
import zipfile
import json
import shutil

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gtk, Gio, GLib, Gdk, GdkPixbuf, WebKit2

from reader_config import (
    APP_ID, TRANSLATIONS_DIR, THEME, OMARCHY_STATE, HOTKEYS, SETTINGS,
    load_theme, load_config, log_import, load_state, save_state,
    load_notes, save_notes, load_settings, save_settings,
    load_prayers, save_prayers, load_memory, save_memory,
    list_translations, _display_name,
)
from reader_assets import FONT_FAMILY, STYLESHEET, PAGE_JS, JS_HANDLER
from document import Document
import personalspace
import verse_ref


class OmarchyReader(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.book_path = None
        self.doc = None             # Document (EpubSource + reading state)
        self._refs_overlay = None
        self._refs_pinned = None
        self._search_overlay = None
        self._search_entry = None
        self._search_list = None
        self._search_results = []
        self._pending_verse = 0
        self._refs_panel_height = 240
        self._search_panel_height = 300
        self._refs_tab = "notes"
        self._ref_tab_buttons = {}
        self.font_size = 18
        self.reading_mode = "dark"
        self._toc_mode = "books"
        self._toc_book_idx = 0
        self._toc_chapter_idx = 0
        self._toc_verse_idx = 0
        self.window = None
        self.webview = None
        self._is_loading = False
        self._theme_monitor = None
        self._parent_monitor = None
        self._monitor_parent_path = None
        self.notes = {}
        self.prayers = []
        self.memory = []
        self._notes_overlay = None
        self._note_panel_height = 300
        self._ps_tab = "notes"
        self._ps_editing = False     # False = navigate mode (keys act), True = typing
        self._ps_tabs = {}
        self._ps_stack = None
        self._active_section = "content"
        self._focus = "content"      # routing truth: content | notes | refs
        self._focus_label = None     # header focus badge
        self._hint_label = None      # header contextual tip
        self._on_home = False
        self._home_options = []
        self._home_sel = 0

    # ---- Reading-state properties backed by Document (single source of truth) ----
    @property
    def source(self):
        return self.doc.source if self.doc else None

    @property
    def chapters(self):
        return self.doc.chapters if self.doc else []

    @property
    def _toc_books(self):
        return self.doc.books if self.doc else []

    @property
    def chapter_index(self):
        return self.doc.chapter_index if self.doc else 0

    @chapter_index.setter
    def chapter_index(self, value):
        if self.doc:
            self.doc.chapter_index = value

    @property
    def current_page(self):
        return self.doc.page if self.doc else 0

    @current_page.setter
    def current_page(self, value):
        if self.doc:
            self.doc.page = value

    @property
    def current_ch(self):
        return self.doc.char_offset if self.doc else 0

    @current_ch.setter
    def current_ch(self, value):
        if self.doc:
            self.doc.char_offset = value

    @property
    def page_count(self):
        return self.doc.pages if self.doc else 0

    @page_count.setter
    def page_count(self, value):
        if self.doc:
            self.doc.pages = value

    @property
    def _current_verse(self):
        return self.doc.verse if self.doc else 0

    @_current_verse.setter
    def _current_verse(self, value):
        if self.doc:
            self.doc.verse = value

    def do_command_line(self, command_line):
        options = command_line.get_arguments()
        self.cli_path = options[1] if len(options) > 1 else None
        self.activate()
        return 0

    def do_activate(self):
        load_theme()
        load_config()
        load_settings()
        self.create_window()
        self.window.present()
        self._start_theme_monitor()
        path = getattr(self, "cli_path", None)
        if path:
            self.open_book(path)

    def do_shutdown(self):
        if self.doc is not None:
            self.doc.close()
            self.doc = None
        Gtk.Application.do_shutdown(self)

    def create_window(self):
        win = Gtk.ApplicationWindow(application=self)
        win.set_title("Omarchy-Bible")
        win.set_default_size(900, 700)

        # Enable real window transparency: the reading surface uses a
        # semi-transparent background so the desktop subtly shows through.
        win.set_app_paintable(True)
        screen = win.get_screen()
        rgba_visual = screen.get_rgba_visual()
        if rgba_visual is not None:
            win.set_visual(rgba_visual)

        self._apply_theme_css()

        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.set_custom_title(self._title_label())

        # Focus indicator lives where "Open Book" used to be (left side).
        self._focus_label = Gtk.Label(label="")
        self._focus_label.get_style_context().add_class("focus-badge")
        self._focus_label.set_visible(False)
        hb.pack_start(self._focus_label)

        self.progress_label = Gtk.Label(label="")
        self.progress_label.get_style_context().add_class("progress-label")

        self.loading_spinner = Gtk.Spinner()
        self.loading_spinner.set_visible(False)
        spinner_box = Gtk.Box(spacing=6)
        spinner_box.pack_start(self.loading_spinner, False, False, 0)
        spinner_box.pack_start(self.progress_label, False, False, 0)

        hb.pack_end(spinner_box)

        hb.pack_end(self._fs_button("A-", self.font_size - 2))
        hb.pack_end(self._fs_button("A+", self.font_size + 2))

        self.headerbar = hb
        win.set_titlebar(hb)
        # Default header visibility is driven by the auto-hide-header setting.
        if SETTINGS.get("auto_hide_header", True):
            self.headerbar.set_visible(False)

        settings = WebKit2.Settings()
        settings.set_enable_javascript(True)
        settings.set_enable_developer_extras(False)
        settings.set_javascript_can_open_windows_automatically(False)

        self.user_content = WebKit2.UserContentManager()
        self.user_content.register_script_message_handler(JS_HANDLER)
        self.user_content.connect(
            "script-message-received::" + JS_HANDLER, self._on_js_message
        )

        self.webview = WebKit2.WebView.new_with_user_content_manager(self.user_content)
        self.webview.set_settings(settings)
        self.webview.set_app_paintable(True)
        self.webview.connect("context-menu", self._suppress_menu)
        self.webview.connect("load-changed", self.on_load_changed)
        self.webview.connect(
            "button-press-event", lambda *_: self._focus_content() or False
        )
        self._apply_webview_bg()
        self._apply_user_stylesheet()

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        scroller.add(self.webview)

        # Native loading overlay (avoids a second load_html call, which raced and
        # intermittently produced a blank window).
        self.loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.loading_box.set_halign(Gtk.Align.CENTER)
        self.loading_box.set_valign(Gtk.Align.CENTER)
        self.loading_box.set_visible(False)
        self.loading_msg = Gtk.Label(label="Loading book…")
        self.loading_msg.get_style_context().add_class("title-label")
        self.loading_sub = Gtk.Label(label="Opening EPUB file")
        self.loading_sub.get_style_context().add_class("progress-label")
        self.loading_box.pack_start(self.loading_msg, False, False, 0)
        self.loading_box.pack_start(self.loading_sub, False, False, 0)

        self.loading_overlay_win = Gtk.Overlay()
        self.loading_overlay_win.add(scroller)
        self.loading_overlay_win.add_overlay(self.loading_box)

        # Table of contents overlay (hidden until toggled).
        self._build_toc_overlay()
        self.loading_overlay_win.add_overlay(self.toc_overlay)

        # Notes overlay (hidden until toggled).
        self._build_notes_overlay()
        self.loading_overlay_win.add_overlay(self._notes_overlay)

        # References panel (study notes; hidden until Ctrl+R).
        self._build_refs_overlay()
        self.loading_overlay_win.add_overlay(self._refs_overlay)

        # Settings overlay (hidden until toggled).
        self._build_settings_overlay()
        self.loading_overlay_win.add_overlay(self._settings_overlay)

        # Keybinding reference overlay (hidden until toggled).
        self._build_help_overlay()
        self.loading_overlay_win.add_overlay(self._help_overlay)

        # Search overlay (opened with "/").
        self._build_search_overlay()
        self.loading_overlay_win.add_overlay(self._search_overlay)

        self.window = win
        win.add(self.loading_overlay_win)
        win.connect("key-press-event", self.on_key_pressed_raw)
        win.connect("set-focus", self._on_set_focus)
        self._load_notes_from_disk()

        self.show_welcome()
        self.window.show_all()
        # Ensure the overlay panels start hidden even though show_all() forces
        # visibility on the whole tree.
        self.loading_box.set_visible(False)
        self.toc_overlay.set_visible(False)
        self._notes_overlay.set_visible(False)
        self._refs_overlay.set_visible(False)
        self._settings_overlay.set_visible(False)
        self._help_overlay.set_visible(False)
        self._search_overlay.set_visible(False)
        # Re-apply header visibility (show_all() blindly re-shows everything).
        if SETTINGS.get("auto_hide_header", True):
            self.headerbar.set_visible(False)

        self._update_header_focus()

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

    # ---------------- Notes ----------------
    def _build_notes_overlay(self):
        """Build the page-specific notes panel (a strip along the bottom)."""
        self._notes_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._notes_overlay.set_visible(False)
        self._notes_overlay.set_halign(Gtk.Align.FILL)
        self._notes_overlay.set_valign(Gtk.Align.END)
        self._notes_overlay.get_style_context().add_class("notes-overlay")

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        bar.set_margin_top(10)
        bar.set_margin_bottom(6)

        self.notes_title = Gtk.Label(label="Personal Space")
        self.notes_title.get_style_context().add_class("title-label")
        self.notes_title.set_halign(Gtk.Align.START)
        bar.pack_start(self.notes_title, False, False, 0)

        self.notes_loc = Gtk.Label(label="")
        self.notes_loc.get_style_context().add_class("progress-label")
        bar.pack_end(self.notes_loc, False, False, 0)

        close = Gtk.Button(label="\u2715")
        close.connect("clicked", lambda *_: self._hide_notes())
        bar.pack_end(close, False, False, 0)

        self._notes_overlay.pack_start(bar, False, False, 0)

        # Tab bar: 1 Notes · 2 Prayer · 3 Memory.
        tabbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tabbar.set_margin_start(16)
        tabbar.set_margin_end(16)
        tabbar.set_margin_bottom(8)
        self._ps_tabs = {}
        for key, label in (("notes", "1 Notes"), ("prayer", "2 Prayer"), ("memory", "3 Memory")):
            b = Gtk.Button(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.connect("clicked", lambda _w, k=key: self._set_ps_tab(k))
            tabbar.pack_start(b, False, False, 0)
            self._ps_tabs[key] = b
        self._notes_overlay.pack_start(tabbar, False, False, 0)

        self._ps_stack = stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.NONE)
        stack.set_vexpand(True)
        stack.set_hexpand(True)

        # ---- Notes page (editor only, per verse) ----
        note_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        note_page.set_margin_start(16)
        note_page.set_margin_end(16)
        note_page.set_margin_bottom(12)
        self.notes_verse_label = Gtk.Label(label="General note")
        self.notes_verse_label.get_style_context().add_class("progress-label")
        self.notes_verse_label.set_xalign(0.0)
        note_page.pack_start(self.notes_verse_label, False, False, 0)
        self.notes_textview = Gtk.TextView()
        self.notes_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.notes_textview.set_vexpand(True)
        self.notes_textview.connect("key-press-event", self._on_note_textview_key)
        self.notes_buffer = self.notes_textview.get_buffer()
        note_page.pack_start(self.notes_textview, True, True, 0)
        nrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add = Gtk.Button(label="Add")
        add.connect("clicked", self._on_note_add)
        nrow.pack_start(add, False, False, 0)
        ndel = Gtk.Button(label="Delete")
        ndel.connect("clicked", lambda *_: self._delete_current_note())
        nrow.pack_start(ndel, False, False, 0)
        nhint = Gtk.Label(label="Ctrl+Enter add \u00b7 Ctrl+J to content \u00b7 1-3 tabs")
        nhint.get_style_context().add_class("progress-label")
        nrow.pack_start(nhint, True, True, 0)
        note_page.pack_start(nrow, False, False, 0)
        stack.add_named(note_page, "notes")

        # ---- Prayer page ----
        prayer_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        prayer_page.set_margin_start(16)
        prayer_page.set_margin_end(16)
        prayer_page.set_margin_bottom(12)
        self.prayer_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pscroller = Gtk.ScrolledWindow()
        pscroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        pscroller.set_vexpand(True)
        pscroller.add(self.prayer_list)
        prayer_page.pack_start(pscroller, True, True, 0)
        prow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.prayer_entry = Gtk.Entry()
        self.prayer_entry.set_placeholder_text("New prayer request\u2026")
        self.prayer_entry.connect("activate", lambda *_: self._prayer_add())
        prow.pack_start(self.prayer_entry, True, True, 0)
        self.prayer_freq = Gtk.ComboBoxText()
        for f in ("daily", "weekly", "monthly"):
            self.prayer_freq.append_text(f.capitalize())
        self.prayer_freq.set_active(0)
        prow.pack_start(self.prayer_freq, False, False, 0)
        padd = Gtk.Button(label="Add")
        padd.connect("clicked", lambda *_: self._prayer_add())
        prow.pack_start(padd, False, False, 0)
        prayer_page.pack_start(prow, False, False, 0)
        stack.add_named(prayer_page, "prayer")

        # ---- Memory page ----
        memory_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        memory_page.set_margin_start(16)
        memory_page.set_margin_end(16)
        memory_page.set_margin_bottom(12)
        self.memory_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        mscroller = Gtk.ScrolledWindow()
        mscroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        mscroller.set_vexpand(True)
        mscroller.add(self.memory_list)
        memory_page.pack_start(mscroller, True, True, 0)
        mrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        madd = Gtk.Button(label="Add current verse")
        madd.connect("clicked", lambda *_: self._memory_add_current())
        mrow.pack_start(madd, False, False, 0)
        self.memory_hide_btn = Gtk.ToggleButton(label="Hide text")
        self.memory_hide_btn.connect("toggled", lambda *_: self._refresh_memory())
        mrow.pack_start(self.memory_hide_btn, False, False, 0)
        mhint = Gtk.Label(label="Follows you across books \u00b7 Space toggles")
        mhint.get_style_context().add_class("progress-label")
        mrow.pack_start(mhint, True, True, 0)
        memory_page.pack_start(mrow, False, False, 0)
        stack.add_named(memory_page, "memory")

        self._notes_overlay.pack_start(stack, True, True, 0)
        self._ps_tab = "notes"
        self._apply_note_height(SETTINGS.get("note_panel_height", 300))

    def _build_refs_overlay(self):
        """Build the tabbed Resources panel (a strip above the notes)."""
        # EventBox gives the panel a real GdkWindow so a click anywhere in it
        # focuses it (its label children are windowless and would not).
        self._refs_overlay = Gtk.EventBox()
        self._refs_overlay.set_visible(False)
        self._refs_overlay.set_halign(Gtk.Align.FILL)
        self._refs_overlay.set_valign(Gtk.Align.END)
        self._refs_overlay.set_size_request(-1, self._refs_panel_height)
        self._refs_overlay.get_style_context().add_class("refs-overlay")
        self._refs_overlay.connect(
            "button-press-event", lambda *_: self._focus_refs()
        )
        self._refs_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._refs_overlay.add(self._refs_inner)
        outer = self._refs_inner

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        bar.set_margin_top(10)
        bar.set_margin_bottom(6)

        title = Gtk.Label(label="Resources")
        title.get_style_context().add_class("title-label")
        title.set_halign(Gtk.Align.START)
        bar.pack_start(title, False, False, 0)

        self.refs_loc = Gtk.Label(label="")
        self.refs_loc.get_style_context().add_class("progress-label")
        bar.pack_end(self.refs_loc, False, False, 0)

        close = Gtk.Button(label="\u2715")
        close.connect("clicked", lambda *_: self._hide_refs())
        bar.pack_end(close, False, False, 0)

        outer.pack_start(bar, False, False, 0)

        # Tab bar: Notes · Cross-refs · Introduction · Images · Links.
        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tabs.set_margin_start(16)
        tabs.set_margin_end(16)
        tabs.set_margin_bottom(8)
        self._ref_tab_buttons = {}
        for key, label in (
            ("notes", "1 Study Notes"),
            ("crossrefs", "2 Cross-refs"),
            ("intro", "3 Intro"),
            ("images", "4 Images"),
            ("links", "5 Links"),
        ):
            btn = Gtk.Button(label=label)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.connect("clicked", lambda _b, k=key: self._set_ref_tab(k))
            tabs.pack_start(btn, False, False, 0)
            self._ref_tab_buttons[key] = btn
        outer.pack_start(tabs, False, False, 0)

        self._refs_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._refs_body.set_margin_start(16)
        self._refs_body.set_margin_end(16)
        self._refs_body.set_margin_bottom(12)
        self._refs_scroller = scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_can_focus(True)
        scroller.connect("button-press-event", lambda *_: self._focus_refs())
        scroller.add(self._refs_body)
        scroller.get_style_context().add_class("refs-scroller")

        outer.pack_start(scroller, True, True, 0)

    def _set_ref_tab(self, key):
        if key not in self._ref_tab_buttons:
            return
        if key != "notes":
            self._refs_pinned = None
        self._refs_tab = key
        self._refresh_refs()
        self._focus_refs()

    def _next_ref_tab(self, delta):
        order = list(self._ref_tab_buttons.keys())
        if self._refs_tab not in order:
            return order[0]
        idx = (order.index(self._refs_tab) + delta) % len(order)
        return order[idx]

    def _sync_ref_tab_buttons(self):
        for key, btn in self._ref_tab_buttons.items():
            ctx = btn.get_style_context()
            if key == self._refs_tab:
                ctx.add_class("tab-active")
            else:
                ctx.remove_class("tab-active")

    def _focus_in_refs(self):
        """True when keyboard focus is inside the Resources panel."""
        if self._refs_overlay is None:
            return False
        widget = self.window.get_focus()
        while widget is not None:
            if widget is self._refs_overlay:
                return True
            if widget is self._notes_overlay or widget is self.toc_overlay:
                return False
            widget = widget.get_parent()
        return False

    def _focus_refs(self):
        self._active_section = "refs"
        self._focus = "refs"
        if self._refs_scroller is not None:
            self._refs_scroller.grab_focus()
        self._update_header_focus()
        return False

    def _focus_content(self):
        self._active_section = "content"
        self._focus = "content"
        if self.webview:
            self.webview.grab_focus()
        self._notes_lose_focus_appearance()
        self._update_header_focus()
        return False

    def _focus_notes(self):
        if not self._notes_overlay.get_visible():
            self._show_notes()
        else:
            self._set_ps_tab(getattr(self, "_ps_tab", "notes"))
        self._active_section = "notes"
        self._focus = "notes"
        self._update_header_focus()
        return False

    def _focus_next(self, forward=True):
        """Move focus among the content and any *currently open* panels.

        Never auto-opens a panel: if only the reading content is present this is
        a no-op. Ctrl+j / Ctrl+k therefore just walk content -> notes -> resources
        (skipping whichever are closed) and back.
        """
        order = ["content"]
        if self._notes_overlay.get_visible():
            order.append("notes")
        if self._refs_overlay is not None and self._refs_overlay.get_visible():
            order.append("refs")
        if len(order) <= 1:
            self._update_header_focus()
            return
        cur = self._focus if self._focus in order else "content"
        idx = order.index(cur)
        nxt = order[(idx + (1 if forward else -1)) % len(order)]
        if nxt == "content":
            self._focus_content()
        elif nxt == "notes":
            self._focus_notes()
        else:
            self._focus_refs()

    def _on_set_focus(self, window, widget):
        """Keep self._focus in sync with real keyboard focus (incl. mouse clicks).

        Whichever panel contains the newly-focused widget becomes the routing
        target, so tab-switch keys and reader navigation stay correct after a
        click just as they do after Ctrl+J/K.
        """
        if widget is None:
            return
        notes = getattr(self, "_notes_overlay", None)
        refs = getattr(self, "_refs_overlay", None)
        w = widget
        while w is not None:
            if w is refs:
                self._focus = "refs"
                self._update_header_focus()
                return
            if w is notes:
                self._focus = "notes"
                # Editing iff a text field (not the tab button) has focus.
                self._ps_editing = widget in (
                    getattr(self, "notes_textview", None),
                    getattr(self, "prayer_entry", None),
                )
                self._update_header_focus()
                return
            w = w.get_parent()
        if widget is self.webview:
            self._focus = "content"
            self._update_header_focus()

    def _scroll_refs(self, delta, big=False):
        if self._refs_scroller is None:
            return
        adj = self._refs_scroller.get_vadjustment()
        step = adj.get_page_increment() if big else adj.get_step_increment()
        if step <= 0:
            step = 240 if big else 30
        new = adj.get_value() + delta * step
        new = max(adj.get_lower(), min(new, adj.get_upper() - adj.get_page_size()))
        adj.set_value(new)

    def _show_refs(self):
        if not self.book_path or not self.chapters:
            return
        self._refs_overlay.set_visible(True)
        self._position_refs_above_notes()
        self._refresh_refs()
        self._focus_refs()

    def _hide_refs(self):
        if self._refs_overlay is not None:
            self._refs_overlay.set_visible(False)
        if self._focus == "refs":
            self._focus = "content"
            self._active_section = "content"
        if self.webview and not self._on_home:
            self.webview.grab_focus()
        self._update_header_focus()

    def _toggle_refs(self):
        if self._refs_overlay is not None and self._refs_overlay.get_visible():
            self._hide_refs()
        else:
            self._show_refs()

    def _position_bottom_panels(self):
        """Stack the bottom-anchored panels: search (bottom) · Personal Space ·
        Resources (top). Each sits just above whichever lower panels are visible,
        so they never overlap and the search bar is always at the very bottom."""
        search_h = (
            self._search_panel_height
            if self._search_overlay is not None and self._search_overlay.get_visible()
            else 0
        )
        notes_h = (
            self._note_panel_height
            if self._notes_overlay is not None and self._notes_overlay.get_visible()
            else 0
        )
        if self._search_overlay is not None:
            self._search_overlay.set_margin_bottom(0)
        if self._notes_overlay is not None:
            self._notes_overlay.set_margin_bottom(search_h)
        if self._refs_overlay is not None:
            self._refs_overlay.set_margin_bottom(search_h + notes_h)

    def _position_refs_above_notes(self):
        # Back-compatible alias; kept so existing call sites still work.
        self._position_bottom_panels()

    def _ref_placeholder(self, text):
        lbl = Gtk.Label(label=text)
        lbl.get_style_context().add_class("progress-label")
        lbl.set_halign(Gtk.Align.START)
        lbl.set_xalign(0.0)
        lbl.set_line_wrap(True)
        self._refs_body.pack_start(lbl, False, False, 0)

    def _ref_card(self, label, text):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.get_style_context().add_class("ref-card")
        if label:
            head = Gtk.Label(label=f"[{label}]")
            head.get_style_context().add_class("toc-current")
            head.set_halign(Gtk.Align.START)
            card.pack_start(head, False, False, 0)
        body = Gtk.Label(label=text)
        body.set_halign(Gtk.Align.START)
        body.set_xalign(0.0)
        body.set_line_wrap(True)
        body.set_selectable(True)
        card.pack_start(body, False, False, 0)
        self._refs_body.pack_start(card, False, False, 0)

    def _open_uri(self, uri):
        """Open a file:// or http(s) URI with the system default handler."""
        try:
            if hasattr(Gtk, "show_uri_on_window"):
                Gtk.show_uri_on_window(self.window, uri, Gdk.CURRENT_TIME)
            else:
                Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception:
            GLib.spawn_command_line_async(f"xdg-open {shlex.quote(uri)}")

    def _refresh_refs(self):
        """Re-render the Resources panel for the current tab + verse."""
        if self._refs_overlay is None or not self._refs_overlay.get_visible():
            return
        for child in self._refs_body.get_children():
            self._refs_body.remove(child)

        tab = self._refs_tab
        verse = getattr(self, "_current_verse", 0)
        book_name = self.chapters[self.chapter_index][0] if 0 <= self.chapter_index < len(self.chapters) else ""

        if tab in ("notes", "crossrefs"):
            self.refs_loc.set_text(f"{book_name} · v. {verse}" if verse else book_name)
            if self._refs_pinned is not None and tab == "notes":
                entries = [self._refs_pinned]
            elif verse and self.source:
                want = "crossrefs" if tab == "crossrefs" else "notes"
                allrefs = self.source.refs(self.chapter_index)
                entries = [e for e in allrefs.get(verse, []) if e.get("kind", "notes") == want]
            else:
                entries = []
            if not entries:
                self._ref_placeholder(
                    "No references for this verse." if verse else "Select a verse with j/k."
                )
            else:
                for e in entries:
                    self._ref_card(e.get("label", ""), e.get("text", ""))
        else:
            intro, images, links = self.source.resources(self.chapter_index) if self.source else ("", [], [])
            self.refs_loc.set_text(book_name)
            if tab == "intro":
                if intro.strip():
                    self._ref_card("", intro)
                else:
                    self._ref_placeholder("No introduction for this book.")
            elif tab == "images":
                if not images:
                    self._ref_placeholder("No images for this book.")
                for im in images:
                    cap = Gtk.Label(label=im.get("caption") or os.path.basename(im["path"]))
                    cap.set_halign(Gtk.Align.START)
                    cap.set_xalign(0.0)
                    cap.set_line_wrap(True)
                    self._refs_body.pack_start(cap, False, False, 0)
                    btn = Gtk.Button()
                    try:
                        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(im["path"], 520, 400, True)
                        btn.add(Gtk.Image.new_from_pixbuf(pix))
                    except Exception:
                        btn.add(Gtk.Label(label="(image unavailable)"))
                    btn.connect(
                        "clicked",
                        lambda _b, p=im["path"]: self._open_uri("file://" + p),
                    )
                    self._refs_body.pack_start(btn, False, False, 0)
            elif tab == "links":
                if not links:
                    self._ref_placeholder("No external links for this book.")
                for lk in links:
                    btn = Gtk.LinkButton(uri=lk["url"], label=lk["text"] or lk["url"])
                    btn.set_halign(Gtk.Align.START)
                    self._refs_body.pack_start(btn, False, False, 0)

        self._refs_overlay.show_all()
        self._refs_overlay.set_visible(True)
        self._position_refs_above_notes()
        self._sync_ref_tab_buttons()

    def _apply_note_height(self, height):
        """Set the height of the notes panel and persist it to settings."""
        self._note_panel_height = int(height)
        if self._notes_overlay is not None:
            self._notes_overlay.set_size_request(-1, self._note_panel_height)
        SETTINGS["note_panel_height"] = self._note_panel_height
        save_settings()
        self._position_refs_above_notes()

    def _grow_note_height(self, amount=40):
        self._apply_note_height(self._note_panel_height + amount)

    def _shrink_note_height(self, amount=40):
        self._apply_note_height(max(160, self._note_panel_height - amount))

    def _apply_refs_height(self, height):
        """Set the height of the references panel."""
        self._refs_panel_height = int(height)
        if self._refs_overlay is not None:
            self._refs_overlay.set_size_request(-1, self._refs_panel_height)
        self._position_refs_above_notes()

    def _grow_refs_height(self, amount=40):
        self._apply_refs_height(self._refs_panel_height + amount)

    def _shrink_refs_height(self, amount=40):
        self._apply_refs_height(max(140, self._refs_panel_height - amount))

    def _notes_lose_focus_appearance(self):
        """Make the Personal Space panel look unfocused without hiding it."""
        self._notes_overlay.get_style_context().remove_class("panel-focused")

    def _edit_from_list(self):
        """Ctrl+l: open Personal Space on the Notes tab for the current verse."""
        if not SETTINGS.get("show_personal_space", True):
            return
        self._active_section = "notes"
        self._show_notes()
        self._set_ps_tab("notes")

    def _set_section(self, section):
        """Set the active focus section: "notes" (Personal Space) or "content"."""
        if not self.book_path or not self.chapters:
            return
        if section == "notes":
            self._focus = "notes"
            self._active_section = "notes"
            self._show_notes()
        elif section == "content":
            self._focus = "content"
            self._active_section = "content"
            self._notes_lose_focus_appearance()
            if self.webview:
                self.webview.grab_focus()
            else:
                self.window.grab_focus()
            self._panel_focus_state()
            self._update_header_focus()

    def _home_apply_highlight(self):
        """Visual feedback for the home-screen j/k selection."""
        if not self._on_home or not self._home_options:
            return
        idx = self._home_sel % len(self._home_options)
        target = f"#home-{idx}"
        js = (
            "var els=document.querySelectorAll('.focused');"
            "for(var i=0;i<els.length;i++)els[i].classList.remove('focused');"
            f"var t=document.querySelector('{target}');"
            "if(t)t.classList.add('focused');"
        )
        self._run_js(js)

    def _home_move(self, delta):
        if len(self._home_options) > 1:
            self._home_sel = (self._home_sel + delta) % len(self._home_options)
        self._home_apply_highlight()

    def _home_activate(self):
        if not self._home_options:
            return
        idx = self._home_sel % len(self._home_options)
        sel = self._home_options[idx]
        if sel == "continue":
            self._continue_reading()
        elif sel == "import":
            self._on_import_epub()
        else:
            self.open_book(os.path.join(TRANSLATIONS_DIR, sel))

    def _home_delete(self):
        """Remove the highlighted translation file (x on the home screen)."""
        if not self._home_options:
            return
        idx = self._home_sel % len(self._home_options)
        sel = self._home_options[idx]
        if sel in ("continue", "import"):
            return
        path = os.path.join(TRANSLATIONS_DIR, sel)
        try:
            if os.path.abspath(path) == os.path.abspath(self.book_path or ""):
                raise ValueError("Close the book before removing it.")
            os.remove(path)
            msg = f"Removed {sel}."
        except Exception as e:
            msg = f"Could not remove {sel}: {e}"
        # Keep the selection near the top of the remaining list.
        self._home_sel = 0
        self.show_welcome(msg)

    def _move_verse(self, direction):
        """J steps down / K steps up through the verses in the current page."""
        delta = 1 if direction == "down" else -1
        self._run_js(f"moveVerse({delta});")

    def _panel_focus_state(self):
        focused = self._focus_in_text_input()
        ov = self._notes_overlay.get_style_context()
        if focused:
            ov.add_class("panel-focused")
        else:
            ov.remove_class("panel-focused")
        return focused

    def _load_notes_from_disk(self):
        self.notes = load_notes()
        self.prayers = load_prayers()
        self.memory = load_memory()

    def _write_notes(self):
        save_notes(self.notes)

    def _note_key(self):
        return self.doc.note_key() if self.doc else None

    def _verse_note_key(self):
        """Per-verse note key for the currently highlighted verse."""
        if not self.doc:
            return None
        return personalspace.note_key(
            self.doc.book_name, self.doc.chapter_index, self._current_verse
        )

    def _note_location_label(self):
        if not self.doc:
            return ""
        book = _display_name(self.doc.book_name) if self.doc.book_name else ""
        base = f"{book} · ch {self.chapter_index + 1}"
        if self._current_verse:
            base += f" · v. {self._current_verse}"
        return base

    def _toggle_notes(self):
        if not SETTINGS.get("show_personal_space", True):
            return
        if self._notes_overlay.get_visible():
            self._hide_notes()
        else:
            self._show_notes()

    def _show_notes(self):
        if not SETTINGS.get("show_personal_space", True):
            return
        if not self.book_path or not self.chapters:
            return
        self._hide_toc()
        self._hide_settings()
        self._hide_help()
        self._notes_overlay.set_visible(True)
        self._notes_overlay.show_all()
        self._set_ps_tab(getattr(self, "_ps_tab", "notes"))
        self._panel_focus_state()
        self._position_refs_above_notes()
        self._focus = "notes"
        self._active_section = "notes"
        self._update_header_focus()

    def _hide_notes(self):
        self._notes_overlay.set_visible(False)
        self._notes_overlay.get_style_context().remove_class("panel-focused")
        self._ps_editing = False
        self._position_refs_above_notes()
        if self._focus == "notes":
            self._focus = "content"
            self._active_section = "content"
        if self.webview and not self._on_home:
            self.webview.grab_focus()
        else:
            self.window.grab_focus()
        self._update_header_focus()

    # ---------------- Personal Space tabs ----------------
    def _set_ps_tab(self, key, edit=False):
        if key not in self._ps_tabs:
            return
        self._ps_tab = key
        self._ps_stack.set_visible_child_name(key)
        for k, b in self._ps_tabs.items():
            ctx = b.get_style_context()
            if k == key:
                ctx.add_class("tab-active")
            else:
                ctx.remove_class("tab-active")
        if key == "notes":
            self._load_verse_note()
        elif key == "prayer":
            self._refresh_prayer()
        elif key == "memory":
            self._refresh_memory()
        # Focus model: by default we focus the tab BUTTON (navigate mode) so
        # 1/2/3, Tab and i work. Only in "edit" mode do we move focus into the
        # text field (so typing goes there instead of switching tabs).
        field = self._ps_text_field(key)
        if edit and field is not None:
            field.grab_focus()
        else:
            self._ps_tabs[key].grab_focus()

    def _ps_text_field(self, key):
        if key == "notes":
            return self.notes_textview
        if key == "prayer":
            return self.prayer_entry
        return None  # memory has no text field

    def _enter_ps_edit(self):
        field = self._ps_text_field(self._ps_tab)
        if field is not None:
            field.grab_focus()
            self._ps_editing = True
            self._update_header_focus()
            return True
        return False

    def _exit_ps_edit(self):
        self._ps_editing = False
        btn = self._ps_tabs.get(self._ps_tab)
        if btn is not None:
            btn.grab_focus()
        self._update_header_focus()

    def _cycle_ps_tab(self, delta):
        order = list(self._ps_tabs.keys())
        if not order:
            return
        idx = order.index(self._ps_tab) if self._ps_tab in order else 0
        self._set_ps_tab(order[(idx + delta) % len(order)], edit=self._ps_editing)

    def _refresh_notes(self):
        """Refresh whichever Personal Space tab is currently active."""
        if self._notes_overlay is None or not self._notes_overlay.get_visible():
            return
        self.notes_loc.set_text(self._note_location_label())
        tab = getattr(self, "_ps_tab", "notes")
        if tab == "notes":
            self._load_verse_note()
        elif tab == "prayer":
            self._refresh_prayer()
        else:
            self._refresh_memory()

    def _load_verse_note(self):
        key = self._verse_note_key()
        self._update_verse_label()
        if key is None:
            return
        lst = self.notes.get(key) or []
        text = ""
        if lst:
            first = lst[0]
            text = first.get("text", "") if isinstance(first, dict) else str(first)
        s, e = self.notes_buffer.get_bounds()
        if self.notes_buffer.get_text(s, e, False) != text:
            self.notes_buffer.set_text(text)

    def _update_verse_label(self):
        if self._current_verse:
            self.notes_verse_label.set_text(f"Verse {self._current_verse}")
        else:
            self.notes_verse_label.set_text("Chapter note (no verse selected)")

    def _on_note_textview_key(self, widget, event):
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._add_note()
            return True
        # The text view would otherwise eat Ctrl+J / Ctrl+K (line feed), so
        # route those to panel focus-cycling here.
        if ctrl and event.keyval in (Gdk.KEY_j, Gdk.KEY_k):
            self._focus_next(forward=(event.keyval == Gdk.KEY_j))
            return True
        return False

    def _on_note_add(self, button):
        self._add_note()

    def _add_note(self):
        key = self._verse_note_key()
        if key is None:
            return
        s, e = self.notes_buffer.get_bounds()
        text = self.notes_buffer.get_text(s, e, False).strip()
        from datetime import datetime
        if not text:
            self.notes.pop(key, None)
        else:
            self.notes[key] = [{
                "text": text,
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "verse": self._current_verse or 0,
            }]
        self._write_notes()
        self._update_verse_label()

    def _delete_current_note(self):
        key = self._verse_note_key()
        if key is not None:
            self.notes.pop(key, None)
            self._write_notes()
        self.notes_buffer.set_text("")

    # ---------------- Prayer requests ----------------
    def _prayer_add(self):
        text = self.prayer_entry.get_text().strip()
        if not text:
            return
        freq = (self.prayer_freq.get_active_text() or "Daily").lower()
        self.prayers = personalspace.add_prayer(self.prayers, text, freq)
        save_prayers(self.prayers)
        self.prayer_entry.set_text("")
        self._refresh_prayer()

    def _prayer_toggle(self, pid):
        self.prayers = personalspace.toggle_prayer(self.prayers, pid)
        save_prayers(self.prayers)
        self._refresh_prayer()

    def _prayer_remove(self, pid):
        self.prayers = personalspace.remove_prayer(self.prayers, pid)
        save_prayers(self.prayers)
        self._refresh_prayer()

    def _refresh_prayer(self):
        if not hasattr(self, "prayer_list"):
            return
        for c in self.prayer_list.get_children():
            self.prayer_list.remove(c)
        if not self.prayers:
            lbl = Gtk.Label(label="No prayer requests yet. Add one below.")
            lbl.get_style_context().add_class("progress-label")
            lbl.set_halign(Gtk.Align.START)
            self.prayer_list.pack_start(lbl, False, False, 0)
        for p in self.prayers:
            pending = personalspace.is_pending(p)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            chk = Gtk.CheckButton()
            chk.set_active(not pending)
            chk.connect("toggled", lambda _w, pid=p["id"]: self._prayer_toggle(pid))
            row.pack_start(chk, False, False, 0)
            label = Gtk.Label(label=f"{p['text']}  [{p.get('freq', 'daily')}]")
            label.set_xalign(0.0)
            label.set_line_wrap(True)
            label.set_hexpand(True)
            if not pending:
                label.get_style_context().add_class("progress-label")
            row.pack_start(label, True, True, 0)
            rm = Gtk.Button(label="Delete")
            rm.set_relief(Gtk.ReliefStyle.NONE)
            rm.connect("clicked", lambda _w, pid=p["id"]: self._prayer_remove(pid))
            row.pack_start(rm, False, False, 0)
            self.prayer_list.pack_start(row, False, False, 0)
        self.prayer_list.show_all()

    # ---------------- Memorization ----------------
    def _memory_add_current(self):
        if not self.doc:
            return
        self._run_js(
            "post({type:'memory_capture', verse: currentVerseNum(),"
            " text: currentVerseText()});"
        )

    def _memory_add(self, verse, text):
        if not self.doc:
            return
        book = _display_name(self.doc.book_name) if self.doc.book_name else self.doc.book_name
        self.memory, _ = personalspace.add_memory(
            self.memory, book, self.chapter_index + 1, verse or 1, text or ""
        )
        save_memory(self.memory)
        self._refresh_memory()

    def _memory_toggle(self, key):
        self.memory = personalspace.toggle_memory(self.memory, key)
        save_memory(self.memory)
        self._refresh_memory()

    def _memory_remove(self, key):
        self.memory = personalspace.remove_memory(self.memory, key)
        save_memory(self.memory)
        self._refresh_memory()

    def _refresh_memory(self):
        if not hasattr(self, "memory_list"):
            return
        for c in self.memory_list.get_children():
            self.memory_list.remove(c)
        hide = self.memory_hide_btn.get_active() if hasattr(self, "memory_hide_btn") else False
        if not self.memory:
            lbl = Gtk.Label(label="No verses yet. Highlight one and 'Add current verse'.")
            lbl.get_style_context().add_class("progress-label")
            lbl.set_halign(Gtk.Align.START)
            self.memory_list.pack_start(lbl, False, False, 0)
        for m in self.memory:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            hrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            chk = Gtk.CheckButton(label=f"{m.get('book', '')} {m.get('chapter', 0)}:{m.get('verse', 0)}")
            chk.set_active(m.get("done", False))
            chk.connect("toggled", lambda _w, k=m["key"]: self._memory_toggle(k))
            hrow.pack_start(chk, True, True, 0)
            rm = Gtk.Button(label="Delete")
            rm.set_relief(Gtk.ReliefStyle.NONE)
            rm.connect("clicked", lambda _w, k=m["key"]: self._memory_remove(k))
            hrow.pack_start(rm, False, False, 0)
            box.pack_start(hrow, False, False, 0)
            txt = Gtk.Label(label="(hidden)" if hide else m.get("text", ""))
            txt.set_xalign(0.0)
            txt.set_line_wrap(True)
            txt.get_style_context().add_class("progress-label")
            box.pack_start(txt, False, False, 0)
            self.memory_list.pack_start(box, False, False, 0)
        self.memory_list.show_all()
    # ---------------- Settings ----------------
    def _build_settings_overlay(self):
        """Build the settings panel (opened with Ctrl+S)."""
        self._settings_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._settings_overlay.set_visible(False)
        self._settings_overlay.set_halign(Gtk.Align.CENTER)
        self._settings_overlay.set_valign(Gtk.Align.CENTER)
        self._settings_overlay.get_style_context().add_class("settings-overlay")
        self._settings_overlay.set_size_request(420, -1)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        bar.set_margin_top(14)
        bar.set_margin_bottom(8)

        title = Gtk.Label(label="Settings")
        title.get_style_context().add_class("title-label")
        title.set_halign(Gtk.Align.START)
        bar.pack_start(title, True, True, 0)

        close = Gtk.Button(label="\u2715")
        close.connect("clicked", lambda *_: self._hide_settings())
        bar.pack_end(close, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_start(16)
        row.set_margin_end(16)
        row.set_margin_top(8)
        row.set_margin_bottom(16)

        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl = Gtk.Label(label="Auto-hide top bar")
        lbl.set_xalign(0.0)
        lbl.set_halign(Gtk.Align.START)
        label_box.pack_start(lbl, False, False, 0)

        sub = Gtk.Label(label="Start with the header hidden; Ctrl+Shift+H shows it.")
        sub.get_style_context().add_class("progress-label")
        sub.set_xalign(0.0)
        sub.set_halign(Gtk.Align.START)
        label_box.pack_start(sub, False, False, 0)

        switch = Gtk.Switch()
        switch.set_active(SETTINGS.get("auto_hide_header", True))
        switch.set_halign(Gtk.Align.END)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("state-set", self._on_auto_hide_toggled)
        self._auto_hide_switch = switch

        row.pack_start(label_box, True, True, 0)
        row.pack_start(switch, False, False, 0)

        self._settings_overlay.pack_start(bar, False, False, 0)
        self._settings_overlay.pack_start(row, False, False, 0)

        # Auto-hide notes.
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row2.set_margin_start(16)
        row2.set_margin_end(16)
        row2.set_margin_top(8)
        row2.set_margin_bottom(8)

        label_box2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl2 = Gtk.Label(label="Auto-hide notes")
        lbl2.set_xalign(0.0)
        lbl2.set_halign(Gtk.Align.START)
        label_box2.pack_start(lbl2, False, False, 0)

        sub2 = Gtk.Label(
            label="Hide the Personal Space panel until Ctrl+P, or always keep it open."
        )
        sub2.get_style_context().add_class("progress-label")
        sub2.set_xalign(0.0)
        sub2.set_halign(Gtk.Align.START)
        sub2.set_line_wrap(True)
        label_box2.pack_start(sub2, False, False, 0)

        switch2 = Gtk.Switch()
        switch2.set_active(SETTINGS.get("auto_hide_notes", True))
        switch2.set_halign(Gtk.Align.END)
        switch2.set_valign(Gtk.Align.CENTER)
        switch2.connect("state-set", self._on_auto_hide_notes_toggled)
        self._auto_hide_notes_switch = switch2

        row2.pack_start(label_box2, True, True, 0)
        row2.pack_start(switch2, False, False, 0)

        self._settings_overlay.pack_start(row2, False, False, 0)

        row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row3.set_margin_start(16)
        row3.set_margin_end(16)
        row3.set_margin_top(8)
        row3.set_margin_bottom(8)

        label_box3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl3 = Gtk.Label(label="Show Personal Space")
        lbl3.set_xalign(0.0)
        lbl3.set_halign(Gtk.Align.START)
        label_box3.pack_start(lbl3, False, False, 0)
        sub3 = Gtk.Label(
            label="Notes, Prayer Requests and Memorization (Ctrl+P). "
            "Turn off to hide the feature entirely."
        )
        sub3.get_style_context().add_class("progress-label")
        sub3.set_xalign(0.0)
        sub3.set_halign(Gtk.Align.START)
        sub3.set_line_wrap(True)
        label_box3.pack_start(sub3, False, False, 0)

        switch3 = Gtk.Switch()
        switch3.set_active(SETTINGS.get("show_personal_space", True))
        switch3.set_halign(Gtk.Align.END)
        switch3.set_valign(Gtk.Align.CENTER)
        switch3.connect("state-set", self._on_show_personal_space_toggled)
        self._show_ps_switch = switch3

        row3.pack_start(label_box3, True, True, 0)
        row3.pack_start(switch3, False, False, 0)

        self._settings_overlay.pack_start(row3, False, False, 0)

    def _on_show_personal_space_toggled(self, switch, active):
        SETTINGS["show_personal_space"] = bool(active)
        save_settings()
        if not active and self._notes_overlay is not None:
            self._hide_notes()
        return False

    def _on_auto_hide_notes_toggled(self, switch, active):
        SETTINGS["auto_hide_notes"] = bool(active)
        save_settings()
        if not active:
            self._show_notes()
        else:
            self._hide_notes()
        return False

    def _on_auto_hide_toggled(self, switch, active):
        SETTINGS["auto_hide_header"] = bool(active)
        save_settings()
        # Apply immediately: when auto-hide is on, hide the header now;
        # when turned off, bring it back so state matches the setting.
        self.headerbar.set_visible(not active)
        return False

    def _toggle_settings(self):
        if self._settings_overlay.get_visible():
            self._hide_settings()
        else:
            self._show_settings()

    def _show_settings(self):
        self._hide_notes()
        self._hide_toc()
        self._hide_help()
        self._settings_overlay.show_all()
        self._settings_overlay.set_visible(True)
        self._auto_hide_switch.set_active(SETTINGS.get("auto_hide_header", True))
        self._auto_hide_notes_switch.set_active(SETTINGS.get("auto_hide_notes", True))

    def _hide_settings(self):
        self._settings_overlay.set_visible(False)

    # ---------------- Keybinding reference ----------------
    def _build_help_overlay(self):
        """Build the keybinding reference panel (opened with Ctrl+K)."""
        self._help_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._help_overlay.set_visible(False)
        self._help_overlay.set_halign(Gtk.Align.CENTER)
        self._help_overlay.set_valign(Gtk.Align.CENTER)
        self._help_overlay.get_style_context().add_class("settings-overlay")
        self._help_overlay.set_size_request(460, -1)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        bar.set_margin_top(14)
        bar.set_margin_bottom(8)

        title = Gtk.Label(label="Keybindings")
        title.get_style_context().add_class("title-label")
        title.set_halign(Gtk.Align.START)
        bar.pack_start(title, True, True, 0)

        close = Gtk.Button(label="\u2715")
        close.connect("clicked", lambda *_: self._hide_help())
        bar.pack_end(close, False, False, 0)

        rows = [
            ("Ctrl + T", "Table of contents: books · chapters · verses (j/k, Enter, h/Back)"),
            ("Ctrl + P", "Personal Space: Notes · Prayer · Memory (tabs 1-3)"),
            ("/", "Search a book or passage (e.g. John 3:16) · Enter jumps there"),
            ("Ctrl + R", "Resources panel: Notes · Cross-refs · Intro · Images · Links"),
            ("1 – 3 / 1 – 5", "Switch tabs in the focused panel (Personal Space / Resources)"),
            ("Ctrl + j / k", "Move focus: content ⇄ personal space ⇄ resources"),
            ("j / k (resources)", "Scroll the Resources panel · h / l switch tabs"),
            ("Ctrl + h / l", "Focus the Personal Space notes editor"),
            ("Ctrl + Shift + H", "Toggle header bar"),
            ("Ctrl + S", "Settings"),
            ("Ctrl + Shift + K", "Keybindings reference"),
            ("Ctrl + [", "Home / choose a translation"),
            ("Ctrl + B", "Toggle reader mode"),
            ("Ctrl + I", "Import an EPUB into the library"),
            ("Ctrl + O", "Open a book file (in the reader)"),
            ("H / L / ← / →", "Previous / next page (left / right)"),
            ("J / K / ↑ / ↓", "Notes highlighted: move up / down · content: step verses (j/↓ down, k/↑ up)"),
            ("Home: j/k, i, x", "Move selection · i imports · x removes a translation"),
            ("x (Home)", "Delete the highlighted translation"),
            ("Ctrl + Shift + +/-", "Grow / shrink the focused panel (Personal Space / Resources)"),
            ("Ctrl + Right / Ctrl + Left", "Traverse chapters"),
        ]
        lines = "\n".join(
            f"<span weight='bold'>{k}</span>{'&#160;' * 4}{v}" for k, v in rows
        )
        body = Gtk.Label()
        body.set_markup(lines)
        body.set_xalign(0.0)
        body.set_halign(Gtk.Align.START)
        body.set_line_wrap(True)
        body.set_margin_start(24)
        body.set_margin_end(24)
        body.set_margin_bottom(20)

        self._help_overlay.pack_start(bar, False, False, 0)
        self._help_overlay.pack_start(body, False, False, 0)

    def _toggle_help(self):
        if self._help_overlay.get_visible():
            self._hide_help()
        else:
            self._show_help()

    def _show_help(self):
        self._hide_notes()
        self._hide_toc()
        self._hide_settings()
        self._help_overlay.show_all()
        self._help_overlay.set_visible(True)

    def _hide_help(self):
        self._help_overlay.set_visible(False)

    # ---------------- Search ("Go to passage") ----------------
    def _build_search_overlay(self):
        # A bottom bar: the input sits at the very bottom and the suggestion
        # list grows upward above it.
        self._search_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._search_overlay.set_visible(False)
        self._search_overlay.set_halign(Gtk.Align.FILL)
        self._search_overlay.set_valign(Gtk.Align.END)
        self._search_overlay.set_size_request(-1, self._search_panel_height)
        self._search_overlay.get_style_context().add_class("search-overlay")

        self._search_list = Gtk.ListBox()
        self._search_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._search_list.connect("row-activated", self._on_search_row_activated)
        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_vexpand(True)
        list_scroll.add(self._search_list)
        self._search_overlay.pack_start(list_scroll, True, True, 0)

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

    def _apply_theme_css(self):
        css = f"""
            window {{
                background-color: {THEME["background"]};
            }}
            headerbar {{
                background-color: {THEME["background"]};
                color: {THEME["foreground"]};
                border-bottom: 1px solid rgba(255,255,255,0.12);
                padding: 0 6px;
            }}
            .title-label {{
                color: {THEME["foreground"]};
                font-weight: bold;
            }}
            .progress-label {{
                color: {THEME["muted"]};
                font-size: 13px;
                margin-right: 8px;
            }}
            .focus-badge {{
                color: {THEME["background"]};
                background-color: {THEME["accent"]};
                border-radius: 10px;
                padding: 1px 8px;
                font-size: 12px;
                font-weight: bold;
            }}
            button {{
                color: {THEME["foreground"]};
                background: transparent;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 8px;
                padding: 2px 8px;
                font-family: {FONT_FAMILY};
            }}
            button:hover {{
                background: rgba(255,255,255,0.08);
            }}
            button:disabled {{
                color: rgba(255,255,255,0.3);
            }}
            .toc-overlay {{
                background-color: alpha({THEME["background"]}, 0.96);
            }}
            .toc-title {{
                color: {THEME["foreground"]};
                padding: 6px 8px;
            }}
            .toc-title.toc-current {{
                color: {THEME["accent"]};
                font-weight: bold;
            }}
            .toc-num {{
                color: {THEME["muted"]};
                font-size: 12px;
            }}
            .toc-overlay row {{
                background: transparent;
            }}
            .toc-overlay row:hover {{
                background: alpha({THEME["accent"]}, 0.12);
            }}
            .toc-overlay row:selected {{
                background-color: alpha({THEME["accent"]}, 0.35);
            }}
            .toc-overlay row:selected .toc-title {{
                color: {THEME["background"]};
                font-weight: bold;
            }}
            .toc-overlay row:selected .toc-current {{
                color: {THEME["background"]};
            }}
            .notes-overlay {{
                background-color: alpha({THEME["background"]}, 0.97);
                border-top: 1px solid rgba(255,255,255,0.15);
            }}
            .notes-overlay.panel-focused {{
                background-color: alpha({THEME["background"]}, 0.88);
            }}
            .notes-overlay .zone-active {{
                border-color: alpha({THEME["accent"]}, 0.8);
            }}
            .notes-overlay.editor-active {{
                background-color: alpha({THEME["background"]}, 0.82);
            }}
            .notes-overlay.list-active {{
                background-color: alpha({THEME["background"]}, 0.82);
            }}
            .note-card {{
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                padding: 10px 12px;
                background: alpha({THEME["accent"]}, 0.08);
            }}
            .note-card.selected {{
                border-color: alpha({THEME["accent"]}, 0.9);
                background: alpha({THEME["accent"]}, 0.30);
            }}
            .note-card label {{
                color: {THEME["foreground"]};
            }}
            .notes-scroller {{
                background-color: alpha({THEME["background"]}, 0.75);
                border-radius: 8px;
                border: 1px solid rgba(255,255,255,0.12);
            }}
            .notes-scroller.zone-active {{
                background-color: alpha({THEME["background"]}, 0.55);
                border-color: alpha({THEME["accent"]}, 0.8);
            }}
            .refs-overlay {{
                background-color: alpha({THEME["background"]}, 0.97);
                border-top: 1px solid rgba(255,255,255,0.15);
                border-bottom: 1px solid rgba(255,255,255,0.10);
            }}
            .refs-scroller {{
                background-color: alpha({THEME["background"]}, 0.55);
                border-radius: 8px;
                border: 1px solid rgba(255,255,255,0.12);
                padding: 4px;
            }}
            .ref-card {{
                border-left: 3px solid alpha({THEME["accent"]}, 0.8);
                padding: 2px 0 2px 10px;
            }}
            .ref-card label {{
                color: {THEME["foreground"]};
            }}
            .refs-overlay button.tab-active, .notes-overlay button.tab-active {{
                background-color: alpha({THEME["accent"]}, 0.28);
                border-radius: 6px;
                font-weight: bold;
            }}
            textview {{
                color: {THEME["foreground"]};
                background-color: rgba(255,255,255,0.05);
                border-radius: 8px;
                border: 1px solid rgba(255,255,255,0.12);
                padding: 4px 6px;
            }}
            textview text {{
                color: {THEME["foreground"]};
                background-color: transparent;
            }}
            .settings-overlay {{
                background-color: alpha({THEME["background"]}, 0.97);
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 12px;
            }}
            .search-overlay {{
                background-color: alpha({THEME["background"]}, 0.98);
                border-top: 1px solid rgba(255,255,255,0.15);
            }}
            .search-overlay entry {{
                font-size: {self.font_size}px;
                background: transparent;
                border: none;
                box-shadow: none;
                padding: 4px 2px;
                color: {THEME["foreground"]};
            }}
            .search-overlay row:selected {{
                background-color: alpha({THEME["accent"]}, 0.35);
                color: {THEME["foreground"]};
                border-radius: 6px;
            }}
            switch {{
                color: {THEME["foreground"]};
            }}
            entry {{
                color: {THEME["foreground"]};
                background-color: rgba(255,255,255,0.05);
                border-radius: 6px;
            }}
            """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _apply_webview_bg(self):
        if getattr(self, "webview", None) is None:
            return
        color = Gdk.RGBA()
        if getattr(self, "reading_mode", "dark") == "light":
            color.parse("#f5f5f4")
        else:
            color.parse(THEME["background"])
        # Semi-transparent so the desktop subtly shows through.
        color.alpha = 0.85
        self.webview.set_background_color(color)

    def _apply_user_stylesheet(self):
        """Inject the reading text color at USER style level.

        The in-page <style> color rules are ignored by WebKitGTK for body
        text in dark mode (text renders black regardless), so we force the
        color through the UserContentManager, which beats page/UA styles.
        """
        if getattr(self, "user_content", None) is None:
            return
        self.user_content.remove_all_style_sheets()
        if getattr(self, "reading_mode", "dark") == "light":
            fg, link = "#1a1a1a", "#1f6feb"
        else:
            fg, link = "#ffffff", "#93c5fd"
        size = getattr(self, "font_size", 18)
        css = (
            "html, body, #container, #container *, #source * "
            "{{ color: {fg} !important; }} "
            "html, body, #container "
            "{{ font-size: {size}px !important; }} "
            "#container a, #container a *, #source a, #source a * "
            "{{ color: {link} !important; }}"
        ).format(fg=fg, link=link, size=size)
        sheet = WebKit2.UserStyleSheet(
            css,
            WebKit2.UserContentInjectedFrames.TOP_FRAME,
            WebKit2.UserStyleLevel.USER,
            None,
            None,
        )
        self.user_content.add_style_sheet(sheet)

    def _start_theme_monitor(self):
        """Watch the live omarchy palette and re-render when the theme changes.

        `omarchy theme set` swaps the active theme atomically: it removes the
        `theme/` directory and moves the new theme in (`rm -rf + mv`). It may
        also write colors.toml in place while a theme is staged. To catch both
        we watch two things:

        * the `theme/` directory itself, for in-place colors.toml writes; and
        * the `current/` parent, for the atomic directory swap. A new instance
          is launched whenever the `theme/` directory is replaced so the first
          watcher keeps working after a swap.
        """
        try:
            self._monitor_parent_path = os.path.join(OMARCHY_STATE, "current")
            # 1. theme/ dir (in-place colors.toml writes)
            theme_dir = Gio.File.new_for_path(
                os.path.join(self._monitor_parent_path, "theme")
            )
            self._theme_monitor = theme_dir.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
            self._theme_monitor.connect("changed", self._on_theme_dir_changed)

            # 2. parent (atomic theme/ dir replacement)
            parent = Gio.File.new_for_path(self._monitor_parent_path)
            self._parent_monitor = parent.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
            self._parent_monitor.connect("changed", self._on_parent_changed)
        except Exception:
            self._theme_monitor = None
            self._parent_monitor = None

    def _on_theme_dir_changed(self, monitor, file, other_file, event):
        if event == Gio.FileMonitorEvent.CHANGES_DONE_HINT:
            return
        GLib.timeout_add(250, self._apply_theme)

    def _on_parent_changed(self, monitor, file, other_file, event):
        if event in (Gio.FileMonitorEvent.CHANGES_DONE_HINT,):
            return
        name = file.get_basename() or ""
        if name == "theme":
            # The theme directory was replaced; re-establish the theme/ watcher
            # so in-place color writes are still observed after a swap.
            if self._theme_monitor is not None:
                try:
                    self._theme_monitor.cancel()
                except Exception:
                    pass
            self._theme_monitor = None
            if self._parent_monitor is not None:
                try:
                    self._parent_monitor.cancel()
                except Exception:
                    pass
            self._parent_monitor = None
            self._start_theme_monitor()
            GLib.timeout_add(250, self._apply_theme)

    def _apply_theme(self):
        load_theme()
        self._apply_theme_css()
        if self.webview is not None:
            self._apply_webview_bg()
            self._apply_user_stylesheet()
        # Re-render the current view so colors are picked up live.
        if self.chapters:
            self._do_load_chapter(self.chapter_index)
        else:
            self.show_welcome()
        return False

    def _title_label(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_halign(Gtk.Align.CENTER)
        self.title_label = Gtk.Label(label="Omarchy-Bible")
        self.title_label.get_style_context().add_class("title-label")
        box.pack_start(self.title_label, False, False, 0)

        # Contextual tip sits next to the title (focus badge is on the left).
        self._hint_label = Gtk.Label(label="")
        self._hint_label.get_style_context().add_class("progress-label")
        self._hint_label.set_visible(False)
        box.pack_start(self._hint_label, False, False, 0)

        self._header_box = box
        return box

    _FOCUS_NAMES = {"content": "Content", "notes": "Personal Space", "refs": "Resources"}

    def _context_hint(self):
        """A short tip for the next useful action, given current state."""
        f = self._focus
        home = getattr(self, "_on_home", False)
        if home or not self.chapters:
            return "Enter open · i import · x remove"
        if f == "notes":
            if self._ps_editing:
                return "Esc back to keys · Ctrl+Enter add note · Ctrl+K to content"
            return "Tab or 1-3 switch tabs · i edit · Ctrl+K to content"
        if f == "refs":
            return "Ctrl+J content · 1-5 tabs · j/k scroll · h/l tabs"
        # content
        tips = []
        if self._notes_overlay.get_visible():
            tips.append("Ctrl+J ⇄ Personal Space")
        elif self._refs_overlay is not None and self._refs_overlay.get_visible():
            tips.append("Ctrl+J ⇄ Resources")
        if not self._notes_overlay.get_visible():
            tips.append("Ctrl+P notes")
        if self._refs_overlay is not None and not self._refs_overlay.get_visible():
            tips.append("Ctrl+R refs")
        return " · ".join(tips) or "Ctrl+P notes · Ctrl+R refs · Ctrl+T contents"

    def _update_header_focus(self):
        if not hasattr(self, "_focus_label") or self._focus_label is None:
            return
        home = getattr(self, "_on_home", False)
        if home or not self.chapters:
            self._focus_label.set_visible(False)
            self._hint_label.set_visible(False)
            return
        name = self._FOCUS_NAMES.get(self._focus, "")
        if name:
            self._focus_label.set_text(f"● {name}")
            self._focus_label.set_visible(True)
        hint = self._context_hint()
        self._hint_label.set_text(hint)
        self._hint_label.set_visible(bool(hint))

    def _fs_button(self, text, target):
        btn = Gtk.Button(label=text)
        btn.connect("clicked", lambda *_: self.change_font_size(target))
        return btn

    def _suppress_menu(self, webview, context_menu, event, hit):
        return True

    def _styles(self, top_padding=60, side_padding=64, bottom_padding=80):
        if getattr(self, "reading_mode", "dark") == "light":
            bg = "#f5f5f4"
            fg = "#1a1a1a"
            muted = "#555555"
            accent = "#1f6feb"
        else:
            bg = THEME["background"]
            fg = "#ffffff"
            muted = THEME["color11"]
            accent = THEME["accent"]
        # The page itself stays transparent in both modes; the semi-transparent
        # webview background (set per-mode) provides the color.
        content_bg = "transparent"
        return STYLESHEET.format(
            background=bg,
            foreground=fg,
            muted=muted,
            accent=accent,
            content_bg=content_bg,
            selection_background=THEME["selection_background"],
            selection_foreground=THEME["selection_foreground"],
            font_family=FONT_FAMILY,
            font_size=self.font_size,
            home_title_fs=self.font_size * 1.8,
            section_fs=self.font_size + 2,
            item_fs=self.font_size + 1,
            top_padding=top_padding,
            side_padding=side_padding,
            bottom_padding=bottom_padding,
        )

    def show_welcome(self, status_msg=None):
        self._is_loading = False
        self._show_spinner(False)
        self.loading_box.set_visible(False)
        self._hide_notes()
        if self._refs_overlay is not None:
            self._refs_overlay.set_visible(False)
        self._hide_settings()
        self._hide_help()
        self._on_home = True
        self._home_options = []
        state = load_state()
        translations = list_translations()
        self._last_status = status_msg or ""

        # Continue-reading row (only if a book/page was previously saved).
        ans = ""
        home_idx = 0
        last_file = state.get("book")
        if last_file and os.path.exists(os.path.join(TRANSLATIONS_DIR, last_file)):
            self._home_options.append("continue")
            chap = state.get("chapter", 1)
            page = state.get("page", 1)
            name = _display_name(last_file)
            ans = f"""
            <div class="section continue">
              <div class="section-title">Continue where you left off</div>
              <a id="home-{home_idx}" class="continue-item" href="javascript:void(0)" data-action="continue">
                <span class="cont-book">{name}</span>
                <span class="cont-pos">Chapter {chap} · Page {page}</span>
                <span class="cont-arrow">&#10148;</span>
              </a>
            </div>"""
            home_idx += 1

        # Translation list.
        if translations:
            items = "".join(
                f'<a id="home-{home_idx + i}" class="book-item" href="javascript:void(0)" data-file="{t}">'
                f'<span class="book-name">{_display_name(t)}</span></a>'
                for i, t in enumerate(translations)
            )
            self._home_options.extend(translations)
            home_idx += len(translations)
        else:
            items = '<div class="empty">No translations found in the <code>translations/</code> folder. Place .epub files there.</div>'

        self._home_options.append("import")
        import_idx = home_idx
        home_idx += 1

        self._home_sel = 0
        status_html = ""
        if getattr(self, "_last_status", ""):
            status_html = f'<div class="home-status">{html.escape(self._last_status)}</div>'

        page_html = f"""<!doctype html><html><head><meta charset="utf-8">
{self._styles()}
<script>
function post(msg) {{
  if (window.webkit && window.webkit.messageHandlers &&
      window.webkit.messageHandlers.omarchy) {{
    try {{ window.webkit.messageHandlers.omarchy.postMessage(JSON.stringify(msg)); }}
    catch (e) {{}}
  }}
}}
document.addEventListener('click', function (e) {{
  var t = e.target.closest('[data-action]') || e.target.closest('[data-file]');
  if (!t) return;
  e.preventDefault();
  var action = t.getAttribute('data-action');
  if (action === 'continue') post({{type:'continue_reading'}});
  else if (action === 'import') post({{type:'import_epub'}});
  else post({{type:'open_book', file: t.getAttribute('data-file')}});
}});
</script>
</head><body class="home-body">
<div class="home">
  <div class="home-title">Omarchy&#8209;Bible</div>
  <div class="home-sub">Choose a Bible translation to begin.</div>
  {status_html}
  {ans}
  <div class="section">
    <div class="section-title">Bible Translations</div>
    <div class="book-list">{items}</div>
  </div>
  <div class="section">
    <div class="section-title">Library</div>
    <a id="home-{import_idx}" class="book-item" href="javascript:void(0)" data-action="import">
      <span class="book-name">Import EPUB</span>
    </a>
  </div>
   <div class="home-footer">j/k select · Enter open · i import · x remove · Ctrl+Shift+K keys</div>
 </div>
 </body></html>"""
        self.webview.load_html(page_html, None)
        self._focus = "content"
        self._update_header_focus()

    def show_loading(self, msg="Opening EPUB file"):
        # Native GTK overlay — no second load_html, so no blank-window race.
        self._is_loading = True
        self._show_spinner(True)
        self.loading_msg.set_text("Loading book…")
        self.loading_sub.set_text(msg)
        self.loading_box.set_visible(True)

    # ---------------- File open ----------------
    def on_open(self, *args):
        dialog = Gtk.FileChooserDialog(
            title="Open EPUB book",
            transient_for=self.window,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.ACCEPT,
        )
        f = Gtk.FileFilter()
        f.set_name("EPUB books (*.epub)")
        f.add_pattern("*.epub")
        dialog.add_filter(f)
        dialog.connect("response", self.on_open_response)
        dialog.show()

    def on_open_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            path = dialog.get_filename()
            dialog.destroy()
            if path:
                self.open_book(path)
        else:
            dialog.destroy()

    def _on_import_epub(self):
        """Let the user pick an EPUB and copy it into the translations folder."""
        log_import("import requested; opening file chooser")
        try:
            dialog = Gtk.FileChooserDialog(
                title="Import EPUB into library",
                transient_for=self.window,
                action=Gtk.FileChooserAction.OPEN,
            )
            dialog.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                "Import", Gtk.ResponseType.ACCEPT,
            )
            f = Gtk.FileFilter()
            f.set_name("EPUB books (*.epub)")
            f.add_pattern("*.epub")
            dialog.add_filter(f)
            dialog.connect("response", self._on_import_response)
            self._import_dialog = dialog
            dialog.show()
            log_import("file chooser shown")
        except Exception as e:
            log_import(f"failed to open chooser: {e!r}")
            GLib.idle_add(self.show_welcome, f"Import failed to start: {e}")

    def _on_import_response(self, dialog, response):
        log_import(f"response={int(response)} ACCEPT={int(Gtk.ResponseType.ACCEPT)}")
        self._import_dialog = None
        if response != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return
        path = dialog.get_filename()
        log_import(f"selected path={path!r}")
        dialog.destroy()
        if not path or not os.path.isfile(path):
            GLib.idle_add(
                self.show_welcome,
                "Import failed: no file was selected.",
            )
            return
        name = os.path.basename(path)
        # Defer the banner to the main loop: painting the webview from inside
        # the response callback (which just destroyed a transient dialog) can
        # silently no-op on some GTK/WebKit builds.
        GLib.idle_add(self._start_import, path, name)

    def _start_import(self, path, name):
        self.show_welcome(f"Importing {name} — checking format…")

        def worker():
            try:
                def is_verse(html):
                    return bool(
                        re.search(r"<sup[^>]*>\d+</sup>", html, flags=re.I)
                        or re.search(
                            r'class="[^"]*(?:verse-num|versenum|chapter-num|bold)[^"]*"'
                            r'[^>]*>[^<]*\d',
                            html,
                            flags=re.I,
                        )
                    )
                with zipfile.ZipFile(path) as zf:
                    entries = [
                        n for n in zf.namelist()
                        if n.lower().endswith((".xhtml", ".html", ".htm"))
                    ]
                    # Prefer scripture-looking files first (e.g. Crossway
                    # bNN.NN.*.text.html), then scan the rest.
                    priority = [n for n in entries if re.search(r'/b\d+\.\d+\..*\.text\.html$', n)]
                    ordered = priority + [n for n in entries if n not in priority]
                    verse_found = False
                    scanned = 0
                    for entry in ordered:
                        try:
                            text = zf.read(entry)[:40000].decode("utf-8", "replace")
                        except Exception:
                            continue
                        scanned += 1
                        if is_verse(text):
                            verse_found = True
                            break
                        if scanned >= 80:
                            break
                if not verse_found:
                    raise ValueError(
                        "No verse numbers found; this file may not be a Bible EPUB."
                    )
                target = os.path.join(TRANSLATIONS_DIR, name)
                if os.path.abspath(path) != os.path.abspath(target):
                    shutil.copy2(path, target)
                msg = f"Imported {name}. Select it below to open."
                log_import(f"import OK: {msg}")
            except Exception as e:
                log_import(f"import error: {e!r}")
                msg = f"Import failed: {e}"
            GLib.idle_add(self._import_done, msg)

        threading.Thread(target=worker, daemon=True).start()
        return False

    def _import_done(self, msg):
        self.show_welcome(msg)
        return False

    def open_book(self, path, resume_index=None, resume_page_num=None):
        self._resume_index = resume_index
        self._resume_page_num = resume_page_num
        self.show_loading()
        # Use idle callback so the loading screen renders before we block on epub.read_epub
        GLib.idle_add(self._open_book_real, path)

    def _open_book_real(self, path):
        try:
            self._on_home = False
            if self.doc is not None:
                self.doc.close()
            doc = Document.open(path)
            if doc is None:
                self.show_welcome()
                return False
            self.doc = doc
            self.book_path = path
            start = 0
            if getattr(self, "_resume_index", None) is not None:
                start = min(self._resume_index, doc.chapter_count() - 1)
            doc.chapter_index = start
            GLib.idle_add(self._do_load_chapter, start)
        except Exception as e:
            self.show_welcome()
            self.progress_label.set_text(f"Error: {e}")
        return False

    # ---------------- Chapter loading ----------------
    def _do_load_chapter(self, index):
        if index < 0 or index >= len(self.chapters):
            return False
        self.chapter_index = index
        _, title, path, _ = self.chapters[index]
        self.title_label.set_text(title)
        self.progress_label.set_text(f"{index + 1}/{len(self.chapters)}")

        body_content = self.source.chapter_html(index)
        base_url = "file://" + os.path.dirname(path) + "/"

        page_html = f"""<!doctype html><html><head><meta charset="utf-8">
{self._styles()}
<script>{PAGE_JS}</script>
</head><body>
<div id="source" style="display:none;">{body_content}</div>
<div id="container"></div>
</body></html>"""

        self._is_loading = True
        self._page_pages = 0
        self._refs_pinned = None
        self._show_spinner(True)
        self.webview.load_html(page_html, base_url)
        return False

    # ---------------- Load events ----------------
    def on_load_changed(self, webview, event):
        if event != WebKit2.LoadEvent.FINISHED:
            return
        if self._on_home:
            self._home_apply_highlight()
        if self._is_loading:
            self._is_loading = False

    def _on_js_message(self, manager, result):
        try:
            msg = result.get_js_value().to_string()
            import json
            data = json.loads(msg)
        except Exception:
            return

        mtype = data.get("type")
        if mtype == "open_book":
            file = data.get("file")
            if file:
                self.open_book(os.path.join(TRANSLATIONS_DIR, file))
        elif mtype == "continue_reading":
            self._continue_reading()
        elif mtype == "import_epub":
            log_import("received import_epub message from webview")
            self._on_import_epub()
        elif mtype == "ready":
            self._is_loading = False
            self._current_verse = 0
            self._refs_pinned = None
            self._update_verse_label()
            self._refresh_refs()
            pages = data.get("pages", 0)
            self.loading_box.set_visible(False)
            self._show_spinner(False)
            self.current_page = 0
            self.current_ch = data.get("pch", 0)
            self.page_count = pages
            self._show_progress(1)

            # Make sure the reader has focus so j/k/h/l are handled by the page
            # as soon as the chapter finishes loading.
            self._focus = "content"
            self._active_section = "content"
            self.webview.grab_focus()
            self._update_header_focus()

            # If resuming into this chapter, jump to the saved page.
            if getattr(self, "_resume_index", None) == self.chapter_index:
                resume_page = getattr(self, "_resume_page_num", None)
                self._resume_index = None
                self._resume_page_num = None
                if resume_page and resume_page > 0:
                    GLib.idle_add(self._run_js, f"showPage({resume_page});")
                    self.current_page = resume_page
                    self._show_progress(resume_page + 1)
                    self._save_state()

            self._save_state()
            # A pending verse (e.g. from search "John 3:16") jumps once the
            # freshly loaded chapter is paginated.
            if getattr(self, "_pending_verse", 0):
                v = self._pending_verse
                self._pending_verse = 0
                GLib.idle_add(self._run_js, f"goToVerse({v});")
            if hasattr(self, "_page_pages"):
                del self._page_pages
            # "Always display" notes: keep the panel open across chapters.
            if not SETTINGS.get("auto_hide_notes", True):
                self._show_notes()
            else:
                self._hide_notes()
                self._refresh_notes()
            if pages <= 0:
                # empty chapter - advance to next non-empty automatically
                self.next_chapter()
        elif mtype == "verse":
            self._current_verse = int(data.get("verse") or 0)
            self._refs_pinned = None
            self._update_verse_label()
            self._refresh_refs()
            self._refresh_notes()
        elif mtype == "ref":
            label = data.get("label", "")
            text = data.get("text", "")
            # A single-letter marker is a cross reference; anything else a note.
            kind = "crossrefs" if len(label) == 1 and label.isalpha() else "notes"
            self._refs_pinned = {"label": label, "text": text, "kind": kind}
            self._refs_tab = kind
            self._show_refs()
            self._refresh_refs()
        elif mtype == "memory_capture":
            self._memory_add(int(data.get("verse") or 0), data.get("text", ""))
        elif mtype == "edge":
            if data.get("dir") == "next":
                self.next_chapter()
            else:
                self.prev_chapter()
        elif mtype == "page":
            self._page_pages = data.get("pages", 0)
            self.current_page = data.get("cur", 0)
            self.current_ch = data.get("pch", 0)
            self.page_count = data.get("pages", 0)
            self._show_progress(data.get("cur", 0) + 1)
            self._save_state()
            self._refresh_notes()
            self._refresh_refs()
        elif mtype == "jserror":
            print("JS error:", data.get("msg"), file=sys.stderr)

    def _save_state(self):
        if not self.doc or not self.book_path:
            return
        save_state(self.doc.to_state())

    def _continue_reading(self):
        state = load_state()
        file = state.get("book")
        if not file:
            self.show_welcome()
            return
        path = os.path.join(TRANSLATIONS_DIR, file)
        if not os.path.isfile(path):
            self.show_welcome()
            return
        # Restore the saved chapter (1-based) and page (1-based) position.
        chapter = max(0, int(state.get("chapter", 1)) - 1)
        page = max(0, int(state.get("page", 1)) - 1)
        self.open_book(path, resume_index=chapter, resume_page_num=page)
        chapter = int(state.get("chapter", 1)) - 1
        page = int(state.get("page", 1)) - 1
        self._resume_index = max(chapter, 0)
        self._resume_page_num = max(page, 0)

    def _show_spinner(self, visible):
        if self.loading_spinner:
            if visible:
                self.loading_spinner.start()
                self.loading_spinner.set_visible(True)
            else:
                self.loading_spinner.stop()
                self.loading_spinner.set_visible(False)
        if self.progress_label:
            self.progress_label.set_text("Loading\u2026" if visible else "")

    def _show_progress(self, page_num):
        parts = []
        if self.chapters:
            parts.append(f"{self.chapter_index + 1}/{len(self.chapters)}")
        if getattr(self, "_page_pages", 0) > 0:
            parts.append(f"p{page_num}/{self._page_pages}")
        self.progress_label.set_text(" \u00b7 ".join(parts))

    def _run_js(self, script, callback=None):
        if self.webview:
            self.webview.evaluate_javascript(
                script, -1, None, None, None, None, callback
            )

    # ---------------- Navigation ----------------
    def next_chapter(self):
        nxt = self.doc.next_index() if self.doc else None
        if nxt is not None:
            self._do_load_chapter(nxt)
            return True
        return False

    def prev_chapter(self):
        prv = self.doc.prev_index() if self.doc else None
        if prv is not None:
            self._do_load_chapter(prv)
            return True
        return False

    def _hotkey_matches(self, binding, keyname, state):
        """Return True when a configured binding (e.g. "Ctrl+equal") matches.

        Bindings are written as modifier + key name, e.g. Ctrl+t, Ctrl+Shift+h,
        or a bare key name like t. Modifiers must match exactly, so a binding
        without Shift never matches a Ctrl+Shift+... press.
        """
        if not binding:
            return False
        parts = [p.strip() for p in binding.split("+")]
        mods = [p.lower() for p in parts[:-1]]
        key = parts[-1]
        if keyname.lower() != key.lower():
            return False
        has_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        has_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if has_ctrl != ("ctrl" in mods):
            return False
        if has_shift != ("shift" in mods):
            return False
        return True

    def _focus_in_text_input(self):
        """Return True when keyboard focus is inside a text-entry widget.

        This lets typing (including SPACE / letter keys) work normally in the
        notes editor instead of being swallowed by the reader's page navigator.
        """
        widget = self.window.get_focus()
        while widget is not None:
            if isinstance(widget, Gtk.Entry):
                return True
            if isinstance(widget, Gtk.TextView):
                return True
            widget = widget.get_parent()
        return False

    def _focus_in_webview(self):
        """Return True when keyboard focus is in the webview reader (not in an overlay)."""
        widget = self.window.get_focus()
        while widget is not None:
            if widget is self.webview:
                return True
            if widget is getattr(self, "_notes_overlay", None):
                return False
            if widget is getattr(self, "toc_overlay", None):
                return False
            widget = widget.get_parent()
        return False

    def on_key_pressed_raw(self, widget, event):
        keyname = Gdk.keyval_name(event.keyval)
        kn = keyname.lower() if keyname else ""
        state = event.state
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # While the search overlay is open, its entry/list own the keyboard.
        if self._search_overlay is not None and self._search_overlay.get_visible():
            return False

        # "/" opens the go-to-passage search (not while typing elsewhere).
        if not ctrl and not shift and kn == "slash" and self.chapters:
            self._open_search()
            return True

        # All keybinding checks use the lower-cased keyname so Shift/CapsLock
        # do not break hotkeys (e.g. Ctrl+L arriving as keyval "L").
        if kn == "escape":
            if self._notes_overlay.get_visible():
                # In edit mode, Escape returns to navigate mode; otherwise close.
                if self._ps_editing:
                    self._exit_ps_edit()
                else:
                    self._hide_notes()
                return True
            if self._refs_overlay is not None and self._refs_overlay.get_visible():
                self._hide_refs()
                return True
            if self._settings_overlay.get_visible():
                self._hide_settings()
                return True
            if self._help_overlay.get_visible():
                self._hide_help()
                return True
            if self.toc_overlay.get_visible():
                if self._toc_mode in ("chapters", "verses"):
                    self._on_toc_back()
                else:
                    self._hide_toc()
                return True
            return False

        # Ctrl+Shift combos and section cycling. Ctrl+h/j/k/l cycle between the
        # three sections (notes list -> add a note -> content); handled while
        # typing too.
        if ctrl:
            # Ctrl+1..3 / Ctrl+Shift+1..5 switch tabs while typing is active.
            if not shift and kn in ("1", "2", "3") and self._notes_overlay.get_visible():
                self._set_ps_tab({"1": "notes", "2": "prayer", "3": "memory"}[kn])
                return True
            if shift and kn in ("1", "2", "3", "4", "5") and self._refs_overlay.get_visible():
                key = {"1": "notes", "2": "crossrefs", "3": "intro",
                       "4": "images", "5": "links"}[kn]
                self._set_ref_tab(key)
                return True
            if not shift and kn == "r":
                # Ctrl+r toggles the reference/commentary panel.
                self._toggle_refs()
                return True
            if not shift and kn == "i":
                # Ctrl+i imports an EPUB into the library.
                self._on_import_epub()
                return True
            if shift and kn == "k":
                self._toggle_help()
                return True
            if shift and kn == "h":
                self._toggle_header()
                return True
            if shift and keyname in ("plus", "equal"):
                if self._focus_in_refs():
                    self._grow_refs_height()
                else:
                    self._grow_note_height()
                return True
            if shift and keyname in ("minus", "underscore"):
                if self._focus_in_refs():
                    self._shrink_refs_height()
                else:
                    self._shrink_note_height()
                return True
            if not shift and kn == "h":
                # Ctrl+h focuses the Personal Space notes editor (works while typing).
                self._edit_from_list()
                return True
            if not shift and kn == "l":
                # Ctrl+l also focuses the Personal Space notes editor for the
                # current verse.
                self._edit_from_list()
                return True
            if not shift and kn in ("j", "k"):
                # Ctrl+j / Ctrl+k move keyboard focus between sections:
                # content <-> personal notes <-> resources.
                self._focus_next(forward=(kn == "j"))
                return True
            if self._hotkey_matches(HOTKEYS.get("toc"), keyname, state):
                self._toggle_toc()
                return True
            if self._hotkey_matches(HOTKEYS.get("note"), keyname, state):
                self._toggle_notes()
                return True
            if self._hotkey_matches(HOTKEYS.get("settings"), keyname, state):
                self._toggle_settings()
                return True
            if self._hotkey_matches(HOTKEYS.get("home_bracket"), keyname, state):
                self.show_welcome()
                return True
            if self._hotkey_matches(HOTKEYS.get("toggle_reader_mode"), keyname, state):
                self._toggle_reader_mode()
                return True
            if self._hotkey_matches(HOTKEYS.get("font_increase"), keyname, state):
                self.change_font_size(self.font_size + 2)
                return True
            if self._hotkey_matches(HOTKEYS.get("font_decrease"), keyname, state):
                self.change_font_size(self.font_size - 2)
                return True
            if self._hotkey_matches(HOTKEYS.get("page_next"), keyname, state):
                self._run_js("nextPage();")
                return True
            if self._hotkey_matches(HOTKEYS.get("page_prev"), keyname, state):
                self._run_js("prevPage();")
                return True
            if kn == "o":
                # Open-file dialog is replaced by Import on the home screen.
                if not self._on_home:
                    self.on_open()
                return True

        # Table of contents: arrow keys + Neo-Vim J/k navigation.
        # h/left goes back a level; l/right are consumed so they don't page the
        # reader behind the overlay.
        if self.toc_overlay.get_visible():
            if kn == "down" or kn == "j":
                self._toc_move(1)
                return True
            if kn == "up" or kn == "k":
                self._toc_move(-1)
                return True
            if kn in ("return", "kp_enter"):
                self._toc_activate_current()
                return True
            if kn in ("h", "left"):
                self._on_toc_back()
                return True
            if kn in ("l", "right"):
                return True

        # Home screen: j/k move the selection, Enter opens, x deletes a
        # translation, i imports an EPUB.
        if self._on_home:
            if kn == "j":
                self._home_move(1)
                return True
            if kn == "k":
                self._home_move(-1)
                return True
            if kn in ("return", "kp_enter"):
                self._home_activate()
                return True
            if kn == "i":
                self._on_import_epub()
                return True
            if kn == "x":
                self._home_delete()
                return True

        # When Personal Space is focused but the user is typing in a text field
        # (notes editor / prayer entry), let plain keys type. Tab switching while
        # typing is done with Ctrl+1/2/3 (handled in the Ctrl block).
        if self._focus == "notes" and self._focus_in_text_input():
            return False

        # Number keys switch tabs for the focused panel: 1-5 for Resources,
        # 1-3 for Personal Space (Notes / Prayer / Memory).
        if not ctrl and not shift and kn in ("1", "2", "3", "4", "5"):
            if self._focus == "refs":
                key = {
                    "1": "notes", "2": "crossrefs", "3": "intro",
                    "4": "images", "5": "links",
                }.get(kn)
                if key:
                    self._set_ref_tab(key)
                    return True
            elif self._focus == "notes":
                key = {"1": "notes", "2": "prayer", "3": "memory"}.get(kn)
                if key:
                    self._set_ps_tab(key)
                    return True

        # Personal Space "navigate" mode (focus is a tab button, not typing):
        # Tab / Shift-Tab cycle tabs, i enters the text field, h/l also switch.
        if self._focus == "notes" and not self._ps_editing:
            if not ctrl and kn == "i":
                self._enter_ps_edit()
                return True
            if kn == "tab":
                self._cycle_ps_tab(-1 if shift else 1)
                return True
            if kn == "backtab":
                self._cycle_ps_tab(-1)
                return True
            if not ctrl and kn == "l":
                self._cycle_ps_tab(1)
                return True
            if not ctrl and kn == "h":
                self._cycle_ps_tab(-1)
                return True

        # When the Resources panel is focused, j/k (and arrows) scroll it and
        # h/l switch tabs — without touching where you are in the content.
        if self._focus == "refs":
            if kn in ("j", "down"):
                self._scroll_refs(1)
                return True
            if kn in ("k", "up"):
                self._scroll_refs(-1)
                return True
            if kn in ("space", "page_down"):
                self._scroll_refs(1, big=True)
                return True
            if kn == "page_up":
                self._scroll_refs(-1, big=True)
                return True
            if kn in ("l", "right"):
                self._set_ref_tab(self._next_ref_tab(1))
                return True
            if kn in ("h", "left"):
                self._set_ref_tab(self._next_ref_tab(-1))
                return True
            return False

        # Content focus: j/k/h/l/arrows are handled by the page's own JS (verse
        # stepping + paging), so let them through. Space/Page keys page here.
        if self._focus == "content":
            if kn in ("j", "k", "up", "down", "h", "l", "left", "right"):
                return False
            if kn == "page_down" or kn == "space":
                self._run_js("nextPage();")
                return True
            if kn == "page_up":
                self._run_js("prevPage();")
                return True
            return False

        # Paging: h/l and arrow/page keys work in any non-content focus too
        # (e.g. Personal Space browsing a tab without a text field).
        if kn in ("h", "left"):
            self._run_js("prevPage();")
            return True
        if kn in ("l", "right"):
            self._run_js("nextPage();")
            return True
        if kn == "page_down" or kn == "space":
            self._run_js("nextPage();")
            return True
        if kn == "page_up":
            self._run_js("prevPage();")
            return True
        return False

    def _toggle_reader_mode(self):
        """Toggle the reading surface between dark (theme) and light modes.

        Dark mode uses the theme's light-on-dark colors with a transparent
        background. Light mode uses dark text on a light background for high
        contrast. Reloads the current view to apply the new colors.
        """
        self.reading_mode = "light" if self.reading_mode != "light" else "dark"
        self._apply_user_stylesheet()
        if getattr(self, "webview", None) is not None:
            if self.chapters:
                self._do_load_chapter(self.chapter_index)
            else:
                self.show_welcome()
        self._apply_webview_bg()

    def _toggle_header(self):
        """Show or hide the header bar (which also hides the window close/X button).

        Hides/shows the headerbar widget itself rather than removing the
        titlebar with set_titlebar(None): removing the titlebar from a live
        CSD window was unreliable (and previously caused the WebKit
        black-page repaint bug). Toggling widget visibility collapses the
        titlebar area without that window churn.
        """
        hb = getattr(self, "headerbar", None)
        if hb is None:
            return
        hb.set_visible(not hb.get_visible())
        self._repaint_webview()

    def _repaint_webview(self):
        """Force the WebKit view to redraw after a window/titlebar relayout.

        Removing/re-adding the CSD titlebar resizes the window; WebKitGTK often
        fails to repaint after that and the view goes black. Queuing a resize
        and a draw nudges it to re-render the current page. We deliberately do
        NOT call show_all() on the overlay here, as that would also re-show the
        hidden loading/TOC overlay panels.
        """
        if self.webview is None:
            return
        self.window.queue_resize()
        GLib.idle_add(lambda: self.webview.queue_draw() or False)

    def change_font_size(self, value):
        self.font_size = max(12, min(34, value))
        self._apply_user_stylesheet()
        if self.chapters:
            self._do_load_chapter(self.chapter_index)
        else:
            self.show_welcome()


def main():
    args = list(sys.argv)
    cli_path = None
    if len(args) > 1:
        candidate = args[1]
        if os.path.isfile(candidate):
            cli_path = candidate
            args = [args[0]]
    app = OmarchyReader()
    app.cli_path = cli_path
    app.run(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Omarchy-Bible failed to start:\n{e}", file=sys.stderr)
        sys.exit(1)
