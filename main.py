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
    list_translations, _display_name,
)
from reader_assets import FONT_FAMILY, STYLESHEET, PAGE_JS, JS_HANDLER
from document import Document


class OmarchyReader(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.book_path = None
        self.doc = None             # Document (EpubSource + reading state)
        self._refs_overlay = None
        self._refs_pinned = None
        self._refs_panel_height = 240
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
        self._notes_overlay = None
        self._note_panel_height = 300
        self._notes_zone = "editor"
        self._note_card_rows = []
        self._highlight_index = -1
        self._active_section = "content"
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

        open_btn = Gtk.Button(label="Open Book")
        open_btn.connect("clicked", self.on_open)
        hb.pack_start(open_btn)

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

        self.window = win
        win.add(self.loading_overlay_win)
        win.connect("key-press-event", self.on_key_pressed_raw)
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
        # Re-apply header visibility (show_all() blindly re-shows everything).
        if SETTINGS.get("auto_hide_header", True):
            self.headerbar.set_visible(False)

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
        if self.webview and not self._on_home:
            self.webview.grab_focus()

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

        self.notes_title = Gtk.Label(label="Personal Notes")
        self.notes_title.get_style_context().add_class("title-label")
        self.notes_title.set_halign(Gtk.Align.START)
        bar.pack_start(self.notes_title, True, True, 0)

        self.notes_loc = Gtk.Label(label="")
        self.notes_loc.get_style_context().add_class("progress-label")
        bar.pack_end(self.notes_loc, False, False, 0)

        close = Gtk.Button(label="\u2715")
        close.connect("clicked", lambda *_: self._hide_notes())
        bar.pack_end(close, False, False, 0)

        # Body: left = notes list, right = entry + add.
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        body.set_margin_start(16)
        body.set_margin_end(16)
        body.set_margin_bottom(12)

        self.notes_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.notes_scroller = scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        scroller.add(self.notes_list)
        scroller.get_style_context().add_class("notes-scroller")
        body.pack_start(scroller, True, True, 0)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.set_valign(Gtk.Align.FILL)
        side.set_size_request(340, -1)

        self.notes_editor_box = side

        # Verse reference label (shows the verse that will be saved with the
        # note, or "General notes" when no verse is highlighted).
        self.notes_verse_label = Gtk.Label(label="General notes")
        self.notes_verse_label.get_style_context().add_class("progress-label")
        self.notes_verse_label.set_xalign(0.0)
        side.pack_start(self.notes_verse_label, False, False, 0)

        self.notes_textview = Gtk.TextView()
        self.notes_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.notes_textview.set_vexpand(True)
        self.notes_textview.connect("key-press-event", self._on_note_textview_key)
        self.notes_buffer = self.notes_textview.get_buffer()
        side.pack_start(self.notes_textview, True, True, 0)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add = Gtk.Button(label="Add")
        add.connect("clicked", self._on_note_add)
        add_row.pack_start(add, False, False, 0)

        hint = Gtk.Label(label="Ctrl+Enter save \u00b7 Ctrl+J to content \u00b7 Ctrl+N close")
        hint.get_style_context().add_class("progress-label")
        add_row.pack_start(hint, True, True, 0)
        side.pack_start(add_row, False, False, 0)

        body.pack_start(side, False, False, 0)

        self._notes_overlay.pack_start(bar, False, False, 0)
        self._notes_overlay.pack_start(body, True, True, 0)
        self._apply_note_height(SETTINGS.get("note_panel_height", 300))

    def _build_refs_overlay(self):
        """Build the tabbed Resources panel (a strip above the notes)."""
        self._refs_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._refs_overlay.set_visible(False)
        self._refs_overlay.set_halign(Gtk.Align.FILL)
        self._refs_overlay.set_valign(Gtk.Align.END)
        self._refs_overlay.set_size_request(-1, self._refs_panel_height)
        self._refs_overlay.get_style_context().add_class("refs-overlay")

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

        self._refs_overlay.pack_start(bar, False, False, 0)

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
        self._refs_overlay.pack_start(tabs, False, False, 0)

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

        self._refs_overlay.pack_start(scroller, True, True, 0)

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
        if self._refs_scroller is not None:
            self._refs_scroller.grab_focus()
        return False

    def _focus_content(self):
        self._active_section = "content"
        if self.webview:
            self.webview.grab_focus()
        self._notes_lose_focus_appearance()
        return False

    def _focus_notes(self):
        if not self._notes_overlay.get_visible():
            self._show_notes()
        self._active_section = "notes"
        self._set_notes_zone("editor")
        return False

    def _focus_in_notes(self):
        """True when keyboard focus is inside the Personal Space / notes panel."""
        if self._notes_overlay is None:
            return False
        widget = self.window.get_focus()
        while widget is not None:
            if widget is self._notes_overlay:
                return True
            widget = widget.get_parent()
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
            return
        if self._focus_in_refs():
            cur = "refs"
        elif self._focus_in_notes():
            cur = "notes"
        else:
            cur = "content"
        idx = order.index(cur) if cur in order else 0
        nxt = order[(idx + (1 if forward else -1)) % len(order)]
        if nxt == "content":
            self._focus_content()
        elif nxt == "notes":
            self._focus_notes()
        else:
            self._focus_refs()

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
        if self.webview and not self._on_home:
            self.webview.grab_focus()

    def _toggle_refs(self):
        if self._refs_overlay is not None and self._refs_overlay.get_visible():
            self._hide_refs()
        else:
            self._show_refs()

    def _position_refs_above_notes(self):
        """Stack the refs panel directly above the notes strip when both are open."""
        if self._refs_overlay is None:
            return
        if self._notes_overlay is not None and self._notes_overlay.get_visible():
            self._refs_overlay.set_margin_bottom(self._note_panel_height)
        else:
            self._refs_overlay.set_margin_bottom(0)

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
        """Make the notes panel look unfocused without hiding it."""
        self._notes_zone = "editor"
        overlay = self._notes_overlay.get_style_context()
        overlay.remove_class("panel-focused")
        overlay.remove_class("editor-active")
        overlay.remove_class("list-active")
        scroller = self.notes_scroller.get_style_context()
        scroller.remove_class("zone-active")
        editor = self.notes_editor_box.get_style_context()
        editor.remove_class("zone-active")

    def _set_section(self, section):
        """Set the active section: \"list\", \"editor\", or \"content\".

        This controls both focus and which keys navigate. Section changes in
        the order list -> editor -> content (cycling).
        """
        if not self.book_path or not self.chapters:
            return
        if section == "list":
            self._editing_note = None
            self._active_section = "notes"
            self._show_notes()
            if self._note_card_rows:
                self._set_notes_zone("list")
            else:
                self._set_notes_zone("editor")
        elif section == "editor":
            self._active_section = "notes"
            self._show_notes()
            self._set_notes_zone("editor")
        elif section == "content":
            self._editing_note = None
            self._active_section = "content"
            self._notes_lose_focus_appearance()
            if self.webview:
                self.webview.grab_focus()
            else:
                self.window.grab_focus()
            self._panel_focus_state()

    def _cycle_section(self):
        """Cycle to the next section: notes list -> add a note -> content."""
        if self._active_section == "content":
            self._set_section("list")
        elif self._notes_zone == "list":
            self._set_section("editor")
        else:
            self._set_section("content")

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
        focused = self._notes_zone == "list" or self._focus_in_text_input()
        ov = self._notes_overlay.get_style_context()
        if focused:
            ov.add_class("panel-focused")
        else:
            ov.remove_class("panel-focused")
        return focused

    def _load_notes_from_disk(self):
        self.notes = load_notes()

    def _write_notes(self):
        save_notes(self.notes)

    def _note_key(self):
        """Stable location key for the current book/chapter/page."""
        return self.doc.note_key() if self.doc else None

    def _legacy_note_keys(self):
        """Older notes.json entries anchored by page number instead of char."""
        return self.doc.legacy_note_keys() if self.doc else []

    def _note_location_label(self):
        if not self.doc:
            return ""
        book = os.path.basename(self.book_path) if self.book_path else ""
        return self.doc.location_label(_display_name(book) if book else "")

    def _toggle_notes(self):
        if self._notes_overlay.get_visible():
            self._hide_notes()
        else:
            self._show_notes()

    def _show_notes(self):
        if not self.book_path or not self.chapters:
            return
        self._hide_toc()
        self._hide_settings()
        self._hide_help()
        self._notes_overlay.set_visible(True)
        self._refresh_notes()
        self._notes_overlay.show_all()
        self._update_verse_label()
        # Focus the editor so typing a note works immediately and plain keys
        # (space, letters, ...) are not stolen by the reader's page navigator.
        self._set_notes_zone("editor")
        self._panel_focus_state()
        self._position_refs_above_notes()

    def _hide_notes(self):
        self._notes_overlay.set_visible(False)
        self._notes_overlay.get_style_context().remove_class("panel-focused")
        self._notes_overlay.get_style_context().remove_class("editor-active")
        self._notes_overlay.get_style_context().remove_class("list-active")
        self._position_refs_above_notes()
        if self.webview and not self._on_home:
            self.webview.grab_focus()
        else:
            self.window.grab_focus()

    def _refresh_notes(self):
        """Rebuild the notes list for the current page."""
        if not hasattr(self, "notes_list") or self._notes_overlay is None:
            return
        if not self._notes_overlay.get_visible():
            return
        key = self._note_key()
        self.notes_loc.set_text(self._note_location_label())
        for child in self.notes_list.get_children():
            self.notes_list.remove(child)
        self._note_card_rows = []
        self._highlight_index = -1

        if key is None:
            empty = Gtk.Label(label="No page selected.")
            empty.get_style_context().add_class("progress-label")
            self.notes_list.pack_start(empty, False, False, 0)
            return

        # Merge the char-anchored key with any legacy page-anchored keys, but
        # dedupe: on page 1 the char offset is 0 so _note_key() already equals
        # the legacy page key — adding both would show every note twice.
        keys = []
        for k in [key] + self._legacy_note_keys():
            if k and k not in keys:
                keys.append(k)
        notes = []
        for k in keys:
            notes.extend(self.notes.get(k, []))
        if not notes:
            empty = Gtk.Label(label="No notes for this page.")
            empty.get_style_context().add_class("progress-label")
            self.notes_list.pack_start(empty, False, False, 0)
            return

        for idx, note in enumerate(notes):
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row.get_style_context().add_class("note-card")
            self._note_card_rows.append(row)
            row.connect("button-press-event", lambda *_, i=idx: self._on_card_clicked(i))

            ntext = note.get("text", note) if isinstance(note, dict) else note
            nts = note.get("ts") if isinstance(note, dict) else ""
            nverse = note.get("verse", 0) if isinstance(note, dict) else 0

            text = Gtk.Label(label=str(ntext))
            text.set_xalign(0.0)
            text.set_line_wrap(True)
            text.set_halign(Gtk.Align.START)
            row.pack_start(text, False, False, 0)

            meta = nts
            if nverse:
                meta = f"{meta} \u00b7 v. {nverse}" if meta else f"v. {nverse}"
            else:
                meta = f"{meta} \u00b7 General notes" if meta else "General notes"
            if meta:
                ts = Gtk.Label(label=meta)
                ts.get_style_context().add_class("progress-label")
                ts.set_xalign(0.0)
                ts.set_halign(Gtk.Align.START)
                row.pack_start(ts, False, False, 0)

            del_btn = Gtk.Button(label="Delete")
            del_btn.set_halign(Gtk.Align.END)
            del_btn.connect("clicked", lambda *_, k=key, i=idx: self._delete_note(k, i))
            row.pack_end(del_btn, False, False, 0)

            self.notes_list.pack_start(row, False, False, 0)

        self.notes_list.show_all()

    def _on_card_clicked(self, index):
        if 0 <= index < len(self._note_card_rows):
            self._highlight_index = index
            self._apply_highlight()
            if self._notes_zone != "list":
                self._set_notes_zone("list")
        return True

    def _set_notes_zone(self, zone):
        """Switch keyboard focus between the notes editor and the notes list."""
        self._notes_zone = zone
        overlay = self._notes_overlay.get_style_context()
        overlay.remove_class("editor-active")
        overlay.remove_class("list-active")
        ov = self._notes_overlay.get_style_context()
        scroller = self.notes_scroller.get_style_context()
        editor = self.notes_editor_box.get_style_context()
        if zone == "editor":
            ov.add_class("editor-active")
            editor.add_class("zone-active")
            scroller.remove_class("zone-active")
            self.notes_textview.grab_focus()
        else:
            ov.add_class("list-active")
            scroller.add_class("zone-active")
            editor.remove_class("zone-active")
            if self._note_card_rows:
                if self._highlight_index < 0:
                    self._highlight_index = 0
                self._apply_highlight()
                self.notes_scroller.grab_focus()
            else:
                self.notes_textview.grab_focus()
                self._notes_zone = "editor"
                self._set_notes_zone("editor")
                return
        self._panel_focus_state()

    def _apply_highlight(self):
        """Visually highlight the currently selected note card."""
        for i, row in enumerate(self._note_card_rows):
            if i == self._highlight_index:
                row.get_style_context().add_class("selected")
            else:
                row.get_style_context().remove_class("selected")

    def _move_highlight(self, delta):
        if not self._note_card_rows:
            return
        n = len(self._note_card_rows)
        self._highlight_index = (self._highlight_index + delta) % n
        self._apply_highlight()
        row = self._note_card_rows[self._highlight_index]
        if hasattr(self, "notes_scroller") and self.notes_scroller.get_vadjustment() is not None:
            adj = self.notes_scroller.get_vadjustment()
            lo, hi = row.get_allocation().y, row.get_allocation().y + row.get_allocation().height
            if adj:
                adj.set_value(min(max(lo, adj.get_value()), max(0, hi - adj.get_page_size())))
            self.notes_scroller.queue_draw()

    def _highlight_note_key_index(self):
        """Map the highlighted row to (key, index) in self.notes, or (None, None).

        The on-screen list merges the char-anchored key with any legacy page
        keys, so walk those lists in the same order to find the real location.
        """
        if self._highlight_index < 0 or not self._note_card_rows:
            return None, None
        key = self._note_key()
        if not key:
            return None, None
        keys = []
        for k in [key] + self._legacy_note_keys():
            if k and k not in keys:
                keys.append(k)
        idx = self._highlight_index
        for k in keys:
            lst = self.notes.get(k)
            if lst is None:
                continue
            if idx < len(lst):
                return k, idx
            idx -= len(lst)
        return None, None

    def _edit_from_list(self):
        """Ctrl+l: load the highlighted note into the add-note editor for
        editing, or open a fresh editor when nothing is highlighted."""
        self._editing_note = None
        key, idx = self._highlight_note_key_index()
        if key is not None and idx is not None:
            lst = self.notes.get(key)
            if lst and 0 <= idx < len(lst):
                self._editing_note = {"key": key, "idx": idx}
                entry = lst[idx]
                text = str(entry.get("text", "")) if isinstance(entry, dict) else str(entry)
                self.notes_buffer.set_text(text)
                end = self.notes_buffer.get_end_iter()
                self.notes_buffer.place_cursor(end)
        self._set_section("editor")

    def _delete_highlighted(self):
        key, idx = self._highlight_note_key_index()
        if key is None or idx is None:
            return
        lst = self.notes.get(key)
        if lst and 0 <= idx < len(lst):
            self._delete_note(key, idx)
            self._move_highlight(0)

    def _on_note_textview_key(self, widget, event):
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._add_note()
            return True
        # The text view would otherwise eat Ctrl+J / Ctrl+K (line feed), so
        # route those to section focus-cycling here.
        if ctrl and event.keyval in (Gdk.KEY_j, Gdk.KEY_k):
            self._focus_next(forward=(event.keyval == Gdk.KEY_j))
            return True
        return False

    def _on_note_add(self, button):
        self._add_note()

    def _update_verse_label(self):
        if self._current_verse:
            self.notes_verse_label.set_text(f"Verse {self._current_verse}")
        else:
            self.notes_verse_label.set_text("General notes")

    def _add_note(self):
        start, end = self.notes_buffer.get_bounds()
        text = self.notes_buffer.get_text(start, end, False).strip()
        self.notes_buffer.set_text("")
        if not text:
            return
        key = self._note_key()
        if key is None:
            return
        from datetime import datetime

        # Editing an existing note loaded via Ctrl+l: update it in place.
        edit = self._editing_note
        self._editing_note = None
        if edit:
            lst = self.notes.get(edit.get("key"))
            idx = edit.get("idx")
            if lst and 0 <= idx < len(lst):
                lst[idx]["text"] = text
                lst[idx]["verse"] = self._current_verse or 0
                self._write_notes()
                self._refresh_notes()
                return
            # The original note is gone (deleted/navigated away): fall
            # through and save a fresh note rather than dropping the text.

        entry = {
            "text": text,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "verse": self._current_verse or 0,
        }
        self.notes.setdefault(key, []).append(entry)
        self._write_notes()
        self._refresh_notes()

    def _delete_note(self, key, index):
        notes = self.notes.get(key)
        if not notes or index >= len(notes):
            return
        del notes[index]
        if not notes:
            self.notes.pop(key, None)
        self._write_notes()
        self._refresh_notes()

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
            label="Hide the notes panel until Ctrl+N, or always keep it open."
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
            ("Ctrl + N", "Personal Notes panel (New button / Ctrl+Enter to add)"),
            ("Ctrl + R", "Resources panel: Notes · Cross-refs · Intro · Images · Links"),
            ("1 – 5", "Switch the Resources panel tab (when open)"),
            ("Ctrl + j / k", "Move focus: content ⇄ personal notes ⇄ resources"),
            ("j / k (resources)", "Scroll the Resources panel · h / l switch tabs"),
            ("Ctrl + h", "Jump to notes list (works while typing)"),
            ("Ctrl + l", "Add a note / edit the highlighted note"),
            ("Ctrl + Shift + H", "Toggle header bar"),
            ("Ctrl + S", "Settings"),
            ("Ctrl + Shift + K", "Keybindings reference"),
            ("Ctrl + [ / Ctrl + P", "Home / choose a translation"),
            ("Ctrl + B", "Toggle reader mode"),
            ("Ctrl + I", "Import an EPUB into the library"),
            ("Ctrl + O", "Open a book file (in the reader)"),
            ("H / L / ← / →", "Previous / next page (left / right)"),
            ("J / K / ↑ / ↓", "Notes highlighted: move up / down · content: step verses (j/↓ down, k/↑ up)"),
            ("Home: j/k, i, x", "Move selection · i imports · x removes a translation"),
            ("x", "Delete the highlighted note (or, on Home, the translation)"),
            ("Ctrl + Shift + +/-", "Grow / shrink the notes or references panel"),
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
            .refs-overlay button.tab-active {{
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
        self.title_label = Gtk.Label(label="Omarchy-Bible")
        self.title_label.get_style_context().add_class("title-label")
        return self.title_label

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
            self.webview.grab_focus()

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
        elif mtype == "ref":
            label = data.get("label", "")
            text = data.get("text", "")
            # A single-letter marker is a cross reference; anything else a note.
            kind = "crossrefs" if len(label) == 1 and label.isalpha() else "notes"
            self._refs_pinned = {"label": label, "text": text, "kind": kind}
            self._refs_tab = kind
            self._show_refs()
            self._refresh_refs()
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

        # All keybinding checks use the lower-cased keyname so Shift/CapsLock
        # do not break hotkeys (e.g. Ctrl+L arriving as keyval "L").
        if kn == "escape":
            if self._notes_overlay.get_visible():
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
                if self._refs_overlay is not None and self._refs_overlay.get_visible():
                    self._grow_refs_height()
                else:
                    self._grow_note_height()
                return True
            if shift and keyname in ("minus", "underscore"):
                if self._refs_overlay is not None and self._refs_overlay.get_visible():
                    self._shrink_refs_height()
                else:
                    self._shrink_note_height()
                return True
            if not shift and kn == "h":
                # Ctrl+h always jumps to the highlight-able notes list
                # (works even while typing a note).
                self._set_section("list")
                return True
            if not shift and kn == "l":
                # Ctrl+l: from a highlighted note, load it for editing in the
                # add-note editor; otherwise open a fresh editor.
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
            if self._hotkey_matches(HOTKEYS.get("home"), keyname, state):
                self.show_welcome()
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

        # While typing in the notes editor, let plain keys type — do not let
        # H/L/J/K/x etc. trigger hotkeys (limit hotkeys while typing). Ctrl+
        # combos and Escape were already handled above.
        if self._focus_in_text_input():
            return False

        # Number keys 1-5 switch the Resources panel tab when it is visible.
        if (
            not ctrl
            and not shift
            and self._refs_overlay is not None
            and self._refs_overlay.get_visible()
            and kn in ("1", "2", "3", "4", "5")
        ):
            key = {"1": "notes", "2": "crossrefs", "3": "intro", "4": "images", "5": "links"}[kn]
            self._set_ref_tab(key)
            return True

        # When the Resources panel is focused, j/k (and arrows) scroll it and
        # h/l switch tabs — without touching where you are in the content.
        if self._focus_in_refs():
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

        # If focus is in the reader webview, let the page's JS handle the
        # reader navigation keys (j/k/up/down step verses, h/l/left/right page).
        # If an overlay is visible, consume those keys instead so the reader
        # doesn't page behind the overlay.
        if self._focus_in_webview():
            if self.toc_overlay.get_visible() or self._notes_overlay.get_visible():
                if kn in ("j", "k", "up", "down", "h", "l", "left", "right"):
                    return True
            elif kn in ("j", "k", "up", "down", "h", "l", "left", "right"):
                return False

        # Notes list navigation: j/k and up/down move the highlight, x deletes.
        # h/l/left/right are consumed so they don't page the reader behind the
        # notes overlay.
        if self._notes_zone == "list" and self._note_card_rows:
            if kn in ("j", "down"):
                self._move_highlight(1)
                return True
            if kn in ("k", "up"):
                self._move_highlight(-1)
                return True
            if kn == "x":
                self._delete_highlighted()
                return True
            if kn in ("h", "l", "left", "right"):
                return True

        # Paging: h/l and arrow/page keys always work.
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
