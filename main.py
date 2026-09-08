#!/usr/bin/env python3
"""OmaBible - a minimal EPUB Bible reader styled after the Omarchy Ash theme."""

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
    list_translations, _display_name, migrate_data_dir,
)
from reader_assets import FONT_FAMILY, STYLESHEET, PAGE_JS, JS_HANDLER
from ui_toc import TocMixin
from ui_search import SearchMixin
from ui_settings import SettingsMixin
from ui_resources import ResourcesMixin
from document import Document
import personalspace
import verse_ref
import lexicon

import re as _re
_VERSE_PARA_RE = _re.compile(r'<p[^>]*><sup[^>]*data-vn="(?P<vn>\d+)"[^>]*>.*?</sup>(?P<body>.*?)</p>', _re.S)

class OmarchyReader(Gtk.Application, TocMixin, SearchMixin, SettingsMixin, ResourcesMixin):
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
        self._search_alloc_height = 0
        self._dock = None
        self._mode_line = None
        self._mode_chips = None
        self._mode_hint = None
        self._pending_verse = 0
        self._prev_search_pos = None   # (book_path, chapter, verse) before a search jump
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
        self._game_sel = 0

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
        migrate_data_dir()
        load_theme()
        load_config()
        load_settings()
        try:
            self.create_window()
        except Exception as e:
            # A failed create_window would otherwise leave a windowless
            # single-instance process that silently swallows later launches.
            print(f"OmaBible failed to start:\n{e}", file=sys.stderr)
            self.quit()
            return
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
        win.set_title("OmaBible")
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
        # Keep the WM a sensible title even with a custom headerbar.
        win.set_title("OmaBible")
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

        # Settings overlay (hidden until toggled).
        self._build_settings_overlay()
        self.loading_overlay_win.add_overlay(self._settings_overlay)

        # Keybinding reference overlay (hidden until toggled).
        self._build_help_overlay()
        self.loading_overlay_win.add_overlay(self._help_overlay)

        # Bottom dock holds the Personal Space, Resources and Search panels
        # plus the always-visible mode line. Built as a single overlay child so
        # GTK stacks them automatically (no manual margin math).
        self._build_notes_overlay()
        self._build_refs_overlay()
        self._build_search_overlay()
        self._build_dock()
        self.loading_overlay_win.add_overlay(self._dock)

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
        self._dock.set_visible(False)
        # Re-apply header visibility (show_all() blindly re-shows everything).
        if SETTINGS.get("auto_hide_header", True):
            self.headerbar.set_visible(False)

        self._update_header_focus()

    # ---------------- Bottom dock + mode line ----------------
    def _build_dock(self):
        # One overlay child; GTK stacks its panels top-to-bottom and the mode
        # line is always the bottom row.
        self._dock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._dock.set_halign(Gtk.Align.FILL)
        self._dock.set_valign(Gtk.Align.END)
        self._dock.set_visible(False)
        self._dock.get_style_context().add_class("dock")
        for panel in (self._refs_overlay, self._notes_overlay, self._search_overlay):
            panel.set_hexpand(True)
            self._dock.pack_start(panel, False, False, 0)
        self._build_mode_line()

    def _build_mode_line(self):
        ml = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        ml.get_style_context().add_class("modeline")
        self._mode_chips = {}
        for key, label in (
            ("content", " CONTENT "),
            ("refs", " RESOURCES "),
            ("notes", " PERSONAL SPACE "),
        ):
            lbl = Gtk.Label(label=label)
            lbl.get_style_context().add_class("modeline-chip")
            eb = Gtk.EventBox()
            eb.add(lbl)
            eb.connect("button-press-event", self._on_mode_chip, key)
            ml.pack_start(eb, False, False, 0)
            self._mode_chips[key] = lbl
        sp = Gtk.Box()
        sp.set_hexpand(True)
        ml.pack_start(sp, True, True, 0)
        self._mode_spinner = Gtk.Spinner()
        self._mode_spinner.set_size_request(14, 14)
        self._mode_spinner.set_visible(False)
        ml.pack_end(self._mode_spinner, False, False, 0)
        self._mode_status = Gtk.Label(label="")
        self._mode_status.get_style_context().add_class("modeline-status")
        ml.pack_end(self._mode_status, False, False, 0)
        self._mode_hint = Gtk.Label(label="")
        self._mode_hint.get_style_context().add_class("modeline-hint")
        ml.pack_end(self._mode_hint, False, False, 0)
        self._mode_line = ml
        self._dock.pack_start(ml, False, False, 0)

    def _on_mode_chip(self, widget, event, key):
        if key == "content":
            self._focus_content()
        elif key == "notes":
            if self._notes_overlay.get_visible():
                self._focus_notes()
            else:
                self._show_notes()
        elif key == "refs":
            if self._refs_overlay.get_visible():
                self._focus_refs()
            else:
                self._show_refs()
        return True

    def _set_section_frame(self, panel, active):
        if panel is None:
            return
        ctx = panel.get_style_context()
        if active:
            ctx.add_class("section-active")
        else:
            ctx.remove_class("section-active")

    def _update_mode_line(self):
        if not hasattr(self, "_mode_chips") or self._mode_chips is None:
            return
        for key, lbl in self._mode_chips.items():
            ctx = lbl.get_style_context()
            if key == self._focus:
                ctx.add_class("modeline-chip-active")
            else:
                ctx.remove_class("modeline-chip-active")
        # Accent frame on the focused dock panel (none when focus is content).
        self._set_section_frame(self._notes_overlay, self._focus == "notes")
        self._set_section_frame(self._refs_overlay, self._focus == "refs")
        self._set_section_frame(
            self._search_overlay,
            self._search_overlay is not None and self._search_overlay.get_visible(),
        )
        if hasattr(self, "_mode_hint"):
            self._mode_hint.set_text(self._context_hint())
        if hasattr(self, "_mode_status"):
            self._mode_status.set_text(self._status_text())

    def _status_text(self):
        home = getattr(self, "_on_home", False)
        if home or not self.chapters:
            return ""
        title = self.chapters[self.chapter_index][1] if 0 <= self.chapter_index < len(self.chapters) else ""
        text = title
        if self._current_verse:
            text += f":{self._current_verse}"
        pages = getattr(self, "_page_pages", 0) or self.page_count
        if pages:
            text += f"   {self.current_page + 1}/{pages}"
        return text

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
        self._ps_tab_names = {}
        for key, name in (("notes", "Notes"), ("prayer", "Prayer"), ("memory", "Memory")):
            self._ps_tab_names[key] = name
            b = Gtk.Button(label=self._tab_label(name, False))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("tab-btn")
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
        add = Gtk.Button(label="[ ADD ]")
        add.get_style_context().add_class("tab-btn")
        add.connect("clicked", self._on_note_add)
        nrow.pack_start(add, False, False, 0)
        ndel = Gtk.Button(label="[ DELETE ]")
        ndel.get_style_context().add_class("tab-btn")
        ndel.connect("clicked", lambda *_: self._delete_current_note())
        nrow.pack_start(ndel, False, False, 0)
        nhint = Gtk.Label(label="i edit \u00b7 Tab cycles \u00b7 Ctrl+Enter add \u00b7 Ctrl+J content")
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
        padd = Gtk.Button(label="[ ADD ]")
        padd.get_style_context().add_class("tab-btn")
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
        madd = Gtk.Button(label="[ ADD VERSE ]")
        madd.get_style_context().add_class("tab-btn")
        madd.connect("clicked", lambda *_: self._memory_add_current())
        mrow.pack_start(madd, False, False, 0)
        self.memory_hide_btn = Gtk.ToggleButton(label="[ HIDE TEXT ]")
        self.memory_hide_btn.get_style_context().add_class("tab-btn")
        self.memory_hide_btn.connect("toggled", self._on_memory_hide_toggled)
        mrow.pack_start(self.memory_hide_btn, False, False, 0)
        mhint = Gtk.Label(label="Follows you across books \u00b7 Space toggles")
        mhint.get_style_context().add_class("progress-label")
        mrow.pack_start(mhint, True, True, 0)
        memory_page.pack_start(mrow, False, False, 0)
        stack.add_named(memory_page, "memory")

        self._notes_overlay.pack_start(stack, True, True, 0)
        self._ps_tab = "notes"
        self._apply_note_height(SETTINGS.get("note_panel_height", 300))

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
        a no-op. Ctrl+j walks content -> resources -> personal space (skipping
        whichever are closed) and wraps; Ctrl+k reverses.
        """
        order = ["content"]
        if self._refs_overlay is not None and self._refs_overlay.get_visible():
            order.append("refs")
        if self._notes_overlay.get_visible():
            order.append("notes")
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

    def _position_bottom_panels(self):
        """Layout is now handled by the dock box; just refresh the focus chrome."""
        self._update_mode_line()

    def _on_search_alloc(self, widget, alloc):
        # Search hugs its content; nothing else needs re-stacking because the
        # dock lays panels out automatically. Keep the hook cheap.
        self._update_mode_line()

    def _position_refs_above_notes(self):
        # Back-compatible alias; kept so existing call sites still work.
        self._update_mode_line()

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
            active = k == key
            ctx = b.get_style_context()
            if active:
                ctx.add_class("tab-active")
            else:
                ctx.remove_class("tab-active")
            b.set_label(self._tab_label(self._ps_tab_names.get(k, k), active))
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
            rm = Gtk.Button(label="[ DEL ]")
            rm.set_relief(Gtk.ReliefStyle.NONE)
            rm.get_style_context().add_class("tab-btn")
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

    def _on_memory_hide_toggled(self, btn):
        btn.set_label("[\u25b8 HIDE TEXT ]" if btn.get_active() else "[ HIDE TEXT ]")
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
            rm = Gtk.Button(label="[ DEL ]")
            rm.set_relief(Gtk.ReliefStyle.NONE)
            rm.get_style_context().add_class("tab-btn")
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
                border-radius: 0;
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
            .dock {{
                background-color: {THEME["background"]};
                border-top: 1px solid rgba(255,255,255,0.12);
                padding: 3px;
            }}
            .modeline {{
                background-color: {THEME["background"]};
                border-top: 1px solid rgba(255,255,255,0.10);
            }}
            .modeline-chip {{
                color: {THEME["muted"]};
                padding: 4px 10px;
                font-size: 12px;
                font-weight: bold;
            }}
            .modeline-chip:hover {{
                color: {THEME["foreground"]};
            }}
            .modeline-chip-active {{
                background-color: {THEME["accent"]};
                color: {THEME["background"]};
            }}
            .modeline-hint {{
                color: {THEME["muted"]};
                font-size: 12px;
                padding: 0 12px;
            }}
            .modeline-status {{
                color: {THEME["foreground"]};
                font-size: 13px;
                font-weight: bold;
                padding: 0 12px;
            }}
            .notes-overlay, .refs-overlay, .search-overlay {{
                border: 1px solid rgba(255,255,255,0.16);
                border-radius: 0;
                margin: 3px;
                background-color: {THEME["background"]};
            }}
            .notes-overlay.section-active,
            .refs-overlay.section-active,
            .search-overlay.section-active {{
                border: 1px solid {THEME["accent"]};
                background-color: alpha({THEME["accent"]}, 0.05);
            }}
            .notes-overlay {{
                background-color: alpha({THEME["background"]}, 0.97);
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
                border-radius: 0;
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
                background-color: transparent;
                border-radius: 0;
                border: none;
            }}
            .notes-scroller.zone-active {{
                background-color: alpha({THEME["background"]}, 0.55);
                border: none;
            }}
            .refs-overlay {{
                background-color: alpha({THEME["background"]}, 0.97);
            }}
            .refs-scroller {{
                background-color: transparent;
                border-radius: 0;
                border: none;
                padding: 4px;
            }}
            .ref-card {{
                border-left: 3px solid alpha({THEME["accent"]}, 0.8);
                padding: 2px 0 2px 10px;
            }}
            .ref-card.word-active {{
                border-left: 3px solid {THEME["accent"]};
                background-color: alpha({THEME["accent"]}, 0.16);
            }}
            .ref-card label {{
                color: {THEME["foreground"]};
            }}
            .tab-btn {{
                background-color: transparent;
                background-image: none;
                border: none;
                box-shadow: none;
                border-radius: 0;
                padding: 2px 4px;
                margin: 0;
                color: {THEME["muted"]};
                font-weight: bold;
            }}
            .tab-btn:hover {{
                color: {THEME["foreground"]};
            }}
            .tab-btn.tab-active {{
                background-color: transparent;
                color: {THEME["accent"]};
            }}
            textview {{
                color: {THEME["foreground"]};
                background-color: transparent;
                border-radius: 0;
                border: none;
                border-bottom: 1px solid alpha({THEME["foreground"]}, 0.35);
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
            }}
            .search-overlay entry {{
                font-size: {self.font_size}px;
                background: transparent;
                border: none;
                border-bottom: 1px solid alpha({THEME["foreground"]}, 0.35);
                box-shadow: none;
                padding: 4px 2px;
                color: {THEME["foreground"]};
            }}
            .search-overlay row:selected {{
                background-color: alpha({THEME["accent"]}, 0.35);
                color: {THEME["foreground"]};
                border-radius: 0;
            }}
            .search-preview {{
                border-top: 1px solid alpha({THEME["accent"]}, 0.5);
                background-color: alpha({THEME["accent"]}, 0.06);
                padding: 8px 16px;
            }}
            .search-preview .preview-ref {{
                color: {THEME["accent"]};
                font-weight: bold;
                font-size: 13px;
            }}
            .search-preview .preview-text {{
                color: {THEME["foreground"]};
                font-size: {self.font_size}px;
            }}
            switch {{
                color: {THEME["foreground"]};
            }}
            entry {{
                color: {THEME["foreground"]};
                background-color: transparent;
                background-image: none;
                border: none;
                border-bottom: 1px solid alpha({THEME["foreground"]}, 0.35);
                border-radius: 0;
                box-shadow: none;
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
        self.title_label = Gtk.Label(label="OmaBible")
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
        self._update_mode_line()
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
        if SETTINGS.get("game_mode"):
            self._show_game_home(status_msg)
            return
        self._is_loading = False
        self._show_spinner(False)
        self.loading_box.set_visible(False)
        self._hide_notes()
        if self._refs_overlay is not None:
            self._refs_overlay.set_visible(False)
        self._hide_settings()
        self._hide_help()
        if self._dock is not None:
            self._dock.set_visible(False)
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

    # ---------------- GameMode home ----------------
    def _show_game_home(self, status_msg=None):
        """Zelda-style library: fires cycle translations, the sword continues."""
        self._is_loading = False
        self._show_spinner(False)
        self.loading_box.set_visible(False)
        self._hide_notes()
        if self._refs_overlay is not None:
            self._refs_overlay.set_visible(False)
        self._hide_settings()
        self._hide_help()
        if self._dock is not None:
            self._dock.set_visible(False)
        self._on_home = True
        self._last_status = status_msg or ""

        translations = list_translations()
        self._home_options = list(translations)
        if not translations:
            self._game_sel = 0
        else:
            self._game_sel %= len(translations)
        self._home_sel = self._game_sel

        sel_file = translations[self._game_sel] if translations else ""
        name = _display_name(sel_file) if sel_file else ""
        sub = "Press i to import an EPUB." if not translations else \
            "Press Enter or click the sword to take this one."
        status_html = ""
        if getattr(self, "_last_status", ""):
            status_html = f'<div class="game-status">{html.escape(self._last_status)}</div>'
        flame_l = f'<svg class="flame" width="44" height="68" viewBox="0 0 40 60"><path d="M20 4 C 27 16 34 22 34 34 C 34 45 26 52 20 56 C 14 52 6 45 6 34 C 6 22 13 16 20 4 Z" fill="#e8a23b"/><path d="M20 18 C 23 26 28 30 28 36 C 28 43 24 48 20 51 C 16 48 12 43 12 36 C 12 30 17 26 20 18 Z" fill="#ffe9a8"/></svg>'
        flame_r = f'<svg class="flame" width="44" height="68" viewBox="0 0 40 60"><path d="M20 4 C 27 16 34 22 34 34 C 34 45 26 52 20 56 C 14 52 6 45 6 34 C 6 22 13 16 20 4 Z" fill="#e8a23b"/><path d="M20 18 C 23 26 28 30 28 36 C 28 43 24 48 20 51 C 16 48 12 43 12 36 C 12 30 17 26 20 18 Z" fill="#ffe9a8"/></svg>'
        sword = """
<svg width="68" height="112" viewBox="0 0 24 40">
  <polygon points="12,0 15,17 9,17" fill="#cfd2d6"/>
  <line x1="12" y1="4" x2="12" y2="14" stroke="#8a8f96" stroke-width="0.8"/>
  <rect x="10.6" y="18" width="2.8" height="7" fill="#b08d3e"/>
  <rect x="6" y="17" width="12" height="2.2" fill="#d4af37"/>
  <circle cx="12" cy="27" r="1.7" fill="#d4af37"/>
</svg>"""
        yn = json.dumps(name)[1:-1] if name else ""

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
  var t = e.target.closest('[data-action]');
  if (!t) return;
  e.preventDefault();
  var action = t.getAttribute('data-action');
  if (action === 'cycle') post({{type:'game_action', action:'cycle', dir: t.getAttribute('data-dir')}});
  else if (action === 'start') post({{type:'game_action', action:'start'}});
}});
</script>
</head><body class="home-body">
<div class="game">
  <div class="game-quote">
    <span class="t">IT&#8217;S DANGEROUS TO GO ALONE!</span>
    <span class="t">TAKE THIS.</span>
  </div>
  {status_html}
  <div class="game-cave">
    <div class="game-fire" data-action="cycle" data-dir="-1" title="Previous translation (h)">{flame_l}</div>
    <a class="game-sword" href="javascript:void(0)" data-action="start" title="Continue reading">
      {sword}
      <span class="game-sword-label" id="game-name">{name}</span>
    </a>
    <div class="game-fire flame-r" data-action="cycle" data-dir="1" title="Next translation (l)">{flame_r}</div>
  </div>
  <div class="game-sub">{sub}</div>
  <div class="game-footer">h / l cycle translation &#183; Enter sword continue &#183; i import &#183; x remove</div>
</div>
</body></html>"""
        self.webview.load_html(page_html, None)
        self._home_sel = self._game_sel
        self._focus = "content"
        self._update_header_focus()

    def _game_home_cycle(self, delta):
        """h/l (fires) select the next/previous translation."""
        translations = list_translations()
        if not translations:
            return
        n = len(translations)
        self._game_sel = (self._game_sel + delta) % n
        self._home_sel = self._game_sel
        name = _display_name(translations[self._game_sel])
        js = (
            "var e=document.getElementById('game-name');"
            "if(e)e.textContent=" + json.dumps(name) + ";"
        )
        self._run_js(js)

    def _game_home_start(self):
        """Sword: continue in the selected translation at the saved spot."""
        translations = list_translations()
        if not translations:
            return
        file = translations[self._game_sel % len(translations)]
        state = load_state()
        path = os.path.join(TRANSLATIONS_DIR, file)
        if state.get("book") == file:
            chapter = max(0, int(state.get("chapter") or 1) - 1)
            page = max(0, int(state.get("page") or 1) - 1)
            self.open_book(path, resume_index=chapter, resume_page_num=page)
        else:
            self.open_book(path)

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

    def _restore_search_origin(self):
        """Jump back to the verse that was reading before the last search jump."""
        prev = getattr(self, "_prev_search_pos", None)
        if not prev:
            return False
        path, idx, verse = prev
        self._prev_search_pos = None
        if not path or idx is None or idx < 0:
            return False
        self._pending_verse = verse or 0
        self._focus = "content"
        if path != getattr(self, "book_path", None):
            # Different book: reopen it at the remembered chapter.
            self.open_book(path, resume_index=idx)
        elif not getattr(self, "chapters", None):
            return False
        else:
            self._do_load_chapter(idx)
            self._focus_content()
        return True

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
        elif mtype == "game_action":
            action = data.get("action")
            if action == "cycle":
                self._game_home_cycle(int(data.get("dir") or 0))
            elif action == "start":
                self._game_home_start()
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
            if self._dock is not None:
                self._dock.set_visible(True)
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
            # Personal Space / Resources stay open across chapter loads; only
            # refresh their content. They are closed via Ctrl+P / Ctrl+R (or the
            # panel's own close button), never implicitly.
            self._refresh_notes()
            if self._refs_overlay is not None and self._refs_overlay.get_visible():
                self._refresh_refs()
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
        if getattr(self, "_mode_spinner", None) is not None:
            if visible:
                self._mode_spinner.start()
                self._mode_spinner.set_visible(True)
            else:
                self._mode_spinner.stop()
                self._mode_spinner.set_visible(False)
        if self.progress_label:
            self.progress_label.set_text("Loading\u2026" if visible else "")
        self._update_mode_line()

    def _show_progress(self, page_num):
        parts = []
        if self.chapters:
            parts.append(f"{self.chapter_index + 1}/{len(self.chapters)}")
        if getattr(self, "_page_pages", 0) > 0:
            parts.append(f"p{page_num}/{self._page_pages}")
        if self.progress_label:
            self.progress_label.set_text(" \u00b7 ".join(parts))
        self._update_mode_line()

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
            if shift and kn in ("1", "2", "3", "4", "5", "6") and self._refs_overlay.get_visible():
                key = {"1": "notes", "2": "crossrefs", "3": "intro",
                       "4": "images", "5": "links", "6": "word"}[kn]
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
            if not shift and kn == "bracketleft":
                # Ctrl+[ exits edit mode (e.g. the notes editor) back to the
                # Personal Space tab/navigate state.
                if self._ps_editing:
                    self._exit_ps_edit()
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

        # Physical Home key returns to the library (only when not typing — a
        # focused text field consumes Home itself to jump the cursor).
        if not ctrl and not shift and kn == "home":
            self.show_welcome()
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
        # translation, i imports an EPUB. In GameMode the fires (h/l) cycle
        # translations and Enter is the sword that starts reading.
        if self._on_home:
            if SETTINGS.get("game_mode"):
                if not ctrl and not shift:
                    if kn in ("h", "left"):
                        self._game_home_cycle(-1)
                        return True
                    if kn in ("l", "right"):
                        self._game_home_cycle(1)
                        return True
                    if kn == "j":
                        self._game_home_cycle(1)
                        return True
                    if kn == "k":
                        self._game_home_cycle(-1)
                        return True
                    if kn in ("return", "kp_enter"):
                        self._game_home_start()
                        return True
                    if kn == "i":
                        self._on_import_epub()
                        return True
                    if kn == "x":
                        self._home_delete()
                        return True
            elif kn == "j":
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
        if not ctrl and not shift and kn in ("1", "2", "3", "4", "5", "6"):
            if self._focus == "refs":
                key = {
                    "1": "notes", "2": "crossrefs", "3": "intro",
                    "4": "images", "5": "links", "6": "word",
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
        # stepping + paging), so let them through.
        if self._focus == "content":
            if not ctrl and not shift and kn == "backspace":
                if self._restore_search_origin():
                    return True
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
    GLib.set_prgname("OmaBible")
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
        print(f"OmaBible failed to start:\n{e}", file=sys.stderr)
        sys.exit(1)
