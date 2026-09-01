#!/usr/bin/env python3
"""Omarchy-Bible - a minimal EPUB Bible reader styled after the Omarchy Ash theme."""

import os
import re
import sys
import json
import tomllib
import tempfile
import shutil

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")

from gi.repository import Gtk, Gio, GLib, Gdk, WebKit2
import ebooklib
from ebooklib import epub

APP_ID = "org.omarchy.Bible"

# Where this project lives (used to locate bundled translations).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRANSLATIONS_DIR = os.path.join(BASE_DIR, "translations")

# User data dir for state + notes.
DATA_DIR = os.path.expanduser("~/.config/omarchy-bible")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
NOTES_PATH = os.path.join(DATA_DIR, "notes.json")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

# Fallback palette (Ash) used when the live omarchy theme cannot be read.
DEFAULT_THEME = {
    "accent": "#626262",
    "foreground": "#e0e0e0",
    "background": "#121212",
    "selection_foreground": "#121212",
    "selection_background": "#e0e0e0",
    "muted": "#b2b2b2",
    "color11": "#b2b2b2",
}

THEME = dict(DEFAULT_THEME)

# Path to the live omarchy theme palette (set by `omarchy theme set`).
OMARCHY_STATE = os.path.expanduser("~/.local/state/omarchy")
OMARCHY_CURRENT_THEME = os.path.join(OMARCHY_STATE, "current", "theme")
OMARCHY_COLORS = os.path.join(OMARCHY_CURRENT_THEME, "colors.toml")


def _parse_colors(text):
    colors = {}
    for line in text.splitlines():
        m = re.match(r'\s*([\w-]+)\s*=\s*"(#[0-9a-fA-F]{3,8})"', line)
        if m:
            colors[m.group(1)] = m.group(2)
    return colors


def load_theme():
    """Read the currently applied omarchy theme colors into the global THEME.

    Falls back to the Ash palette when the live palette is missing.
    """
    target = {}
    if os.path.isfile(OMARCHY_COLORS):
        try:
            with open(OMARCHY_COLORS, "r", encoding="utf-8") as fh:
                colors = _parse_colors(fh.read())
            if colors:
                picked = {}
                picked["background"] = colors.get("background")
                picked["foreground"] = colors.get("foreground")
                picked["accent"] = colors.get("accent")
                picked["selection_background"] = colors.get("selection_background", colors.get("cursor"))
                picked["selection_foreground"] = colors.get("selection_foreground", colors.get("background"))
                picked["muted"] = colors.get("color11") or colors.get("color7")
                picked["color11"] = picked["muted"]
                target = {k: (v or DEFAULT_THEME[k]) for k, v in picked.items()}
        except Exception:
            target = dict(DEFAULT_THEME)
    else:
        target = dict(DEFAULT_THEME)
    THEME.clear()
    THEME.update(target)
    return True


# ~/.config/omarchy-bible/config.toml  (user-editable hotkeys)
CONFIG_PATH = os.path.expanduser("~/.config/omarchy-bible/config.toml")

DEFAULT_HOTKEYS = {
    "font_increase": "Ctrl+equal",
    "font_decrease": "Ctrl+minus",
    "toc": "Ctrl+t",
    "toggle_header": "Ctrl+h",
    "toggle_reader_mode": "Ctrl+b",
    "page_next": "Ctrl+Right",
    "page_prev": "Ctrl+Left",
    "note": "Ctrl+n",
    "home": "Ctrl+p",
    "settings": "Ctrl+s",
}

HOTKEYS = dict(DEFAULT_HOTKEYS)


def load_config():
    """Load user hotkeys from ~/.config/omarchy-reader/config.toml.

    Only values actually provided in the file override the defaults, so a
    partial config file is fine. Missing/invalid files keep the defaults.
    """
    global HOTKEYS
    config = {}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "rb") as fh:
                config = tomllib.load(fh)
        except Exception:
            config = {}
    hotkeys = config.get("hotkeys", {}) if isinstance(config, dict) else {}
    merged = dict(DEFAULT_HOTKEYS)
    if isinstance(hotkeys, dict):
        for key, value in hotkeys.items():
            if isinstance(value, str) and value.strip():
                merged[key] = value.strip()
    HOTKEYS = merged
    return True


# ---------------- State & notes persistence ----------------

def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_state():
    """Return the persisted reading state dict (book, chapter, page)."""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_state(state):
    _ensure_data_dir()
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except Exception:
        pass


def load_notes():
    """Return notes dict: {location_key: [note, ...]}."""
    try:
        with open(NOTES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_notes(notes):
    _ensure_data_dir()
    try:
        with open(NOTES_PATH, "w", encoding="utf-8") as fh:
            json.dump(notes, fh, indent=2)
    except Exception:
        pass


# ---------------- Settings persistence ----------------

DEFAULT_SETTINGS = {
    "auto_hide_header": True,
}

SETTINGS = dict(DEFAULT_SETTINGS)


def load_settings():
    """Load persisted app settings into the global SETTINGS dict.

    Missing/partial files keep the defaults. JSON is used here for consistency
    with the other persisted data files (state.json / notes.json).
    """
    data = {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
    except Exception:
        data = {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    SETTINGS.clear()
    SETTINGS.update(merged)
    return SETTINGS


def save_settings():
    _ensure_data_dir()
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(SETTINGS, fh, indent=2)
    except Exception:
        pass


def list_translations():
    """Return a sorted list of EPUB filenames in the translations folder."""
    if not os.path.isdir(TRANSLATIONS_DIR):
        return []
    return sorted(
        f for f in os.listdir(TRANSLATIONS_DIR)
        if f.lower().endswith(".epub")
    )


def _display_name(filename):
    name = os.path.splitext(filename)[0]
    # "ub-EASV" -> "EASV", "KJV.epub" -> "KJV"
    name = name.split("-")[-1]
    return name


def current_theme_name():
    try:
        with open(os.path.join(OMARCHY_STATE, "current", "theme.name"), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return "Unknown"


FONT_FAMILY = "JetBrainsMono Nerd Font"

STYLESHEET = """
<style>
  html, body {{
    margin: 0;
    padding: 0;
    background: {content_bg} !important;
    color: {foreground} !important;
    font-family: {font_family}, "JetBrains Mono", monospace;
    font-size: {font_size}px;
    line-height: 1.7;
    overflow: hidden;
    height: 100%;
  }}
  #container, .page, #source {{
    background: {content_bg} !important;
  }}
  #container {{
    color: {foreground} !important;
  }}
  #container p, #container span, #container li, #container td, #container div {{
    color: {foreground} !important;
  }}
  body {{
    box-sizing: border-box;
    padding: 0 {side_padding}px;
  }}
  h1, h2, h3, h4, h5, h6 {{
    color: {accent};
    line-height: 1.3;
    margin: 1.4em 0 0.6em;
  }}
  p {{ margin: 0 0 1.1em; }}
  a {{ color: {foreground}; text-decoration: underline; }}
  blockquote {{
    border-left: 3px solid {accent};
    margin: 1em 0;
    padding-left: 1em;
    color: {muted};
  }}
  img {{ max-width: 100%; height: auto; border-radius: 4px; }}
  code {{
    background: rgba(255,255,255,0.07);
    padding: 0.15em 0.4em;
    border-radius: 3px;
    font-size: 0.9em;
  }}
  pre {{
    background: rgba(255,255,255,0.05);
    padding: 1em;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 0.9em;
  }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{
    border: 1px solid rgba(255,255,255,0.15);
    padding: 0.4em 0.6em;
  }}
  hr {{ border: none; border-top: 1px solid rgba(255,255,255,0.2); margin: 1.5em 0; }}
  ::selection {{ background: {selection_background}; color: {selection_foreground}; }}

  .page {{
    position: absolute;
    left: 0;
    right: 0;
    top: 0;
    box-sizing: border-box;
    padding: {top_padding}px {side_padding}px {bottom_padding}px;
    opacity: 0;
    transition: opacity 0.15s ease;
  }}
  .page.active {{ opacity: 1; }}
  #container {{
    position: absolute;
    left: 0; right: 0; top: 0; bottom: 0;
    overflow: hidden;
  }}
  #loading-overlay {{
    position: fixed;
    left: 0; right: 0; bottom: 0;
    height: 3px;
    z-index: 9999;
    background: rgba(255,255,255,0.08);
    overflow: hidden;
  }}
  #loading-overlay .bar {{
    height: 100%;
    width: 30%;
    background: {accent};
    animation: loading-slide 1.2s ease-in-out infinite;
  }}
  @keyframes loading-slide {{
    0% {{ transform: translateX(-100%); }}
    100% {{ transform: translateX(400%); }}
  }}

  /* ---- Home / library screen ---- */
  body.home-body {{
    overflow: auto;
    padding: 0;
    background: {content_bg} !important;
  }}
  .home {{
    max-width: 760px;
    margin: 0 auto;
    padding: 56px {side_padding}px 80px;
  }}
  .home-title {{
    font-size: {home_title_fs}px;
    color: {accent};
    font-weight: bold;
    margin-bottom: 4px;
  }}
  .home-sub {{
    color: {muted};
    margin-bottom: 28px;
  }}
  .section {{
    margin-bottom: 30px;
  }}
  .section-title {{
    font-size: {section_fs}px;
    color: {muted};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 12px;
    border-bottom: 1px solid rgba(255,255,255,0.12);
    padding-bottom: 6px;
  }}
  .book-list {{
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  .book-item {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    color: {foreground};
    text-decoration: none;
    background: rgba(255,255,255,0.02);
    transition: background 0.15s ease, border-color 0.15s ease;
  }}
  .book-item:hover {{
    background: alpha({accent}, 0.12);
    border-color: {accent};
  }}
  .book-name {{
    font-size: {item_fs}px;
    font-weight: bold;
  }}
  .empty {{
    color: {muted};
    padding: 12px 2px;
  }}
  .continue {{
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 10px;
    padding: 14px 16px;
    background: alpha({accent}, 0.08);
  }}
  .continue .section-title {{
    border-bottom: none;
    margin-bottom: 6px;
  }}
  .continue-item {{
    display: flex;
    align-items: center;
    gap: 12px;
    color: {foreground};
    text-decoration: none;
  }}
  .cont-book {{
    font-weight: bold;
    font-size: {item_fs}px;
  }}
  .cont-pos {{
    color: {muted};
    font-size: 13px;
  }}
  .cont-arrow {{
    margin-left: auto;
    color: {accent};
    font-size: 18px;
  }}
  .home-footer {{
    margin-top: 34px;
    color: {muted};
    font-size: 12px;
    opacity: 0.7;
    text-align: center;
  }}
</style>
"""

PAGE_JS = r"""
var state = { pages: 0, current: 0, ready: false, reported: false };

function post(msg) {
  if (window.webkit && window.webkit.messageHandlers &&
      window.webkit.messageHandlers.omarchy) {
    try { window.webkit.messageHandlers.omarchy.postMessage(JSON.stringify(msg)); }
    catch (e) {}
  }
}

function nodeToHtml(node) {
  if (node.nodeType === Node.TEXT_NODE) return node.nodeValue;
  return node.outerHTML;
}

function paginate() {
  var source = document.getElementById('source');
  var container = document.getElementById('container');
  if (!source || !container) return 0;

  var viewport = document.documentElement.clientHeight;
  var topPad = 60, bottomPad = 100, sidePad = 64;
  var pageHeight = viewport - topPad - bottomPad;
  if (pageHeight < 100) return 0;

  var width = document.body.clientWidth - sidePad * 2;
  if (width < 100) width = 600;

  var nodes = Array.prototype.slice.call(source.childNodes);
  var pieces = nodes.map(nodeToHtml).filter(function (s) { return s && s.trim(); });
  if (pieces.length === 0) { state.pages = 0; state.ready = true; return 0; }

  var probe = document.createElement('div');
  probe.className = 'page';
  probe.style.position = 'absolute';
  probe.style.left = '-99999px';
  probe.style.visibility = 'hidden';
  probe.style.boxSizing = 'border-box';
  probe.style.width = width + 'px';
  probe.style.padding = topPad + 'px ' + sidePad + 'px ' + bottomPad + 'px';
  document.body.appendChild(probe);

  function measure(html) {
    probe.innerHTML = html;
    return probe.scrollHeight;
  }

  var pages = [];
  var block = '';
  for (var i = 0; i < pieces.length; i++) {
    var candidate = block + pieces[i];
    if (block && measure(candidate) > pageHeight) {
      pages.push(block);
      block = pieces[i];
    } else {
      block = candidate;
    }
  }
  if (block) pages.push(block);
  document.body.removeChild(probe);

  state.pages = pages.length;
  container.innerHTML = '';
  for (var p = 0; p < pages.length; p++) {
    var div = document.createElement('div');
    div.className = 'page' + (p === 0 ? ' active' : '');
    div.innerHTML = pages[p];
    container.appendChild(div);
  }
  state.current = 0;
  state.ready = true;
  window.scrollTo(0, 0);
  return pages.length;
}

function report() {
  post({type:'ready', pages: state.pages});
}

function currentPageIdx() { return state.current; }

function showPage(idx) {
  if (!state.ready || state.pages === 0) return 0;
  if (idx < 0) idx = 0;
  if (idx >= state.pages) idx = state.pages - 1;
  var pages = document.querySelectorAll('.page');
  for (var i = 0; i < pages.length; i++) {
    pages[i].className = i === idx ? 'page active' : 'page';
  }
  state.current = idx;
  window.scrollTo(0, 0);
  return idx;
}

function nextPage() {
  if (!state.ready) return -2;
  if (state.pages === 0) { post({type:'edge', dir:'next'}); return -1; }
  if (state.current + 1 < state.pages) {
    showPage(state.current + 1);
    post({type:'page', cur:state.current, pages:state.pages});
    return state.current;
  }
  post({type:'edge', dir:'next'});
  return -1;
}

function prevPage() {
  if (!state.ready) return -2;
  if (state.pages === 0) { post({type:'edge', dir:'prev'}); return -1; }
  if (state.current - 1 >= 0) {
    showPage(state.current - 1);
    post({type:'page', cur:state.current, pages:state.pages});
    return state.current;
  }
  post({type:'edge', dir:'prev'});
  return -1;
}

function gotoNextChapter() {
  post({type:'edge', dir:'next'});
}

function gotoPrevChapter() {
  post({type:'edge', dir:'prev'});
}

// Bootstrap: retry pagination until content lays out, then report exactly once.
(function () {
  var tries = 0, reported = false;
  function attempt() {
    if (reported) return;
    var n = 0;
    try { n = paginate(); } catch (e) { post({type:'jserror', msg: 'paginate: ' + e.message}); }
    if (n > 0) { reported = true; report(); return; }
    var src = document.getElementById('source');
    var empty = !src || src.childNodes.length === 0 ||
                (src.innerHTML && src.innerHTML.replace(/\s/g, '').length === 0);
    if (empty || tries >= 30) { reported = true; report(); return; }
    tries++;
    setTimeout(attempt, 120);
  }
  window.addEventListener('load', function () { setTimeout(attempt, 120); });
  window.addEventListener('resize', function () { setTimeout(attempt, 120); });
})();
"""

JS_HANDLER = "omarchy"


class OmarchyReader(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.book = None
        self.bookdir = None
        self.book_path = None
        self.chapters = []
        self.chapter_index = 0
        self.current_page = 0
        self.page_count = 0
        self.font_size = 18
        self.reading_mode = "dark"
        self.window = None
        self.webview = None
        self._is_loading = False
        self._theme_monitor = None
        self._parent_monitor = None
        self._monitor_parent_path = None
        self.notes = {}
        self._notes_overlay = None

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

        # Settings overlay (hidden until toggled).
        self._build_settings_overlay()
        self.loading_overlay_win.add_overlay(self._settings_overlay)

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
        self._settings_overlay.set_visible(False)
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

        label = Gtk.Label(label="Table of Contents")
        label.get_style_context().add_class("title-label")
        label.set_halign(Gtk.Align.START)
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

        self.toc_overlay.pack_start(bar, False, False, 0)
        self.toc_overlay.pack_start(scroller, True, True, 0)

    def _refresh_toc(self):
        """(Re)populate the chapter list from the current book."""
        rows = self.toc_list.get_children()
        for r in rows:
            self.toc_list.remove(r)
        self._toc_row_index = {}
        self._toc_rows = []
        for i, chapter in enumerate(self.chapters):
            _, title, _, _ = chapter
            row = Gtk.ListBoxRow()
            self._toc_row_index[row] = i
            self._toc_rows.append(row)
            num = Gtk.Label(label=str(i + 1))
            num.get_style_context().add_class("toc-num")
            num.set_width_chars(4)
            num.set_xalign(1.0)
            num.set_valign(Gtk.Align.START)

            txt = Gtk.Label(label=title or f"Chapter {i + 1}")
            txt.set_xalign(0.0)
            txt.set_line_wrap(True)
            txt.set_halign(Gtk.Align.START)
            txt.get_style_context().add_class("toc-title")
            if i == self.chapter_index:
                txt.get_style_context().add_class("toc-current")

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.pack_start(num, False, False, 0)
            box.pack_start(txt, True, True, 0)
            row.add(box)
            self.toc_list.add(row)
        self.toc_count.set_text(
            f"{len(self.chapters)} chapter{'s' if len(self.chapters) != 1 else ''}"
        )

    def _toggle_toc(self):
        if self.toc_overlay.get_visible():
            self._hide_toc()
        else:
            self._show_toc()

    def _show_toc(self):
        if not self.chapters:
            return
        self._hide_notes()
        self._hide_settings()
        self._refresh_toc()
        self.toc_overlay.show_all()
        self.toc_overlay.set_visible(True)
        # Highlight + focus the current chapter row so arrow/J/k navigation
        # has a starting point.
        current = min(self.chapter_index, len(self._toc_rows) - 1)
        if self._toc_rows:
            self.toc_list.select_row(self._toc_rows[current])
            self.toc_list.grab_focus()

    def _hide_toc(self):
        self.toc_overlay.set_visible(False)

    def _toc_selected_index(self):
        row = self.toc_list.get_selected_row()
        if row is not None:
            return self._toc_row_index.get(row)
        # Fall back to the current chapter if nothing is selected yet.
        if self._toc_rows:
            return self.chapter_index
        return None

    def _toc_move(self, offset):
        current = self._toc_selected_index()
        if current is None or not self._toc_rows:
            return
        target = current + offset
        if target < 0 or target >= len(self._toc_rows):
            return
        self.toc_list.select_row(self._toc_rows[target])
        self.toc_list.scroll_to_row(self._toc_rows[target])

    def _toc_activate_current(self):
        if not self.toc_overlay.get_visible():
            return
        row = self.toc_list.get_selected_row()
        if row is not None:
            self._on_toc_row_activated(self.toc_list, row)

    def _on_toc_row_activated(self, listbox, row):
        index = self._toc_row_index.get(row)
        if index is None:
            return
        self._hide_toc()
        if 0 <= index < len(self.chapters) and index != self.chapter_index:
            self._do_load_chapter(index)

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

        self.notes_title = Gtk.Label(label="Notes")
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
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        scroller.add(self.notes_list)
        body.pack_start(scroller, True, True, 0)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.set_valign(Gtk.Align.CENTER)
        side.set_size_request(320, -1)

        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.notes_entry = Gtk.Entry()
        self.notes_entry.set_placeholder_text("Add a note for this page\u2026")
        self.notes_entry.connect("activate", self._on_note_entry_activated)
        entry_row.pack_start(self.notes_entry, True, True, 0)

        add = Gtk.Button(label="Add")
        add.connect("clicked", self._on_note_add)
        entry_row.pack_start(add, False, False, 0)
        side.pack_start(entry_row, False, False, 0)

        hint = Gtk.Label(label="Ctrl+N to close")
        hint.get_style_context().add_class("progress-label")
        hint.set_halign(Gtk.Align.START)
        side.pack_start(hint, False, False, 0)

        body.pack_start(side, False, False, 0)

        self._notes_overlay.pack_start(bar, False, False, 0)
        self._notes_overlay.pack_start(body, True, True, 0)
        self._notes_overlay.set_size_request(-1, 260)

    def _load_notes_from_disk(self):
        self.notes = load_notes()

    def _write_notes(self):
        save_notes(self.notes)

    def _note_key(self):
        """Location key for the current book/chapter/page."""
        if not self.book_path or not self.chapters:
            return None
        book = os.path.basename(self.book_path)
        return f"{book}|{self.chapter_index}|{self.current_page}"

    def _note_location_label(self):
        book = os.path.basename(self.book_path) if self.book_path else ""
        name = _display_name(book) if book else ""
        return f"{name} · ch {self.chapter_index + 1} · page {self.current_page + 1}"

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
        self._refresh_notes()
        self._notes_overlay.show_all()
        self._notes_overlay.set_visible(True)
        # Focus the entry so typing a note works immediately and pointer/space
        # keys are not stolen by the reader's page navigator.
        self.notes_entry.grab_focus()

    def _hide_notes(self):
        self._notes_overlay.set_visible(False)
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

        if key is None:
            empty = Gtk.Label(label="No page selected.")
            empty.get_style_context().add_class("progress-label")
            self.notes_list.pack_start(empty, False, False, 0)
            return

        notes = self.notes.get(key, [])
        if not notes:
            empty = Gtk.Label(label="No notes for this page.")
            empty.get_style_context().add_class("progress-label")
            self.notes_list.pack_start(empty, False, False, 0)
            return

        for idx, note in enumerate(notes):
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row.get_style_context().add_class("note-card")

            text = Gtk.Label(label=note)
            text.set_xalign(0.0)
            text.set_line_wrap(True)
            text.set_halign(Gtk.Align.START)
            row.pack_start(text, False, False, 0)

            del_btn = Gtk.Button(label="Delete")
            del_btn.set_halign(Gtk.Align.END)
            del_btn.connect("clicked", lambda *_, k=key, i=idx: self._delete_note(k, i))
            row.pack_end(del_btn, False, False, 0)

            self.notes_list.pack_start(row, False, False, 0)

        self.notes_list.show_all()

    def _on_note_entry_activated(self, entry):
        self._add_note()

    def _on_note_add(self, button):
        self._add_note()

    def _add_note(self):
        text = self.notes_entry.get_text().strip()
        self.notes_entry.set_text("")
        if not text:
            return
        key = self._note_key()
        if key is None:
            return
        self.notes.setdefault(key, []).append(text)
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

        sub = Gtk.Label(label="Start with the header hidden; Ctrl+H shows it.")
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
        self._settings_overlay.show_all()
        self._settings_overlay.set_visible(True)
        self._auto_hide_switch.set_active(SETTINGS.get("auto_hide_header", True))

    def _hide_settings(self):
        self._settings_overlay.set_visible(False)

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
            .note-card {{
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                padding: 10px 12px;
                background: alpha({THEME["accent"]}, 0.08);
            }}
            .note-card label {{
                color: {THEME["foreground"]};
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

    def show_welcome(self):
        self._is_loading = False
        self._show_spinner(False)
        self.loading_box.set_visible(False)
        self._hide_notes()
        self._hide_settings()
        state = load_state()
        translations = list_translations()

        # Continue-reading row (only if a book/page was previously saved).
        ans = ""
        last_file = state.get("book")
        if last_file and os.path.exists(os.path.join(TRANSLATIONS_DIR, last_file)):
            chap = state.get("chapter", 1)
            page = state.get("page", 1)
            name = _display_name(last_file)
            ans = f"""
            <div class="section continue">
              <div class="section-title">Continue where you left off</div>
              <a class="continue-item" href="javascript:void(0)" data-action="continue">
                <span class="cont-book">{name}</span>
                <span class="cont-pos">Chapter {chap} · Page {page}</span>
                <span class="cont-arrow">&#10148;</span>
              </a>
            </div>"""

        # Translation list.
        if translations:
            items = "".join(
                f'<a class="book-item" href="javascript:void(0)" data-file="{t}">'
                f'<span class="book-name">{_display_name(t)}</span></a>'
                for t in translations
            )
        else:
            items = '<div class="empty">No translations found in the <code>translations/</code> folder. Place .epub files there.</div>'

        html = f"""<!doctype html><html><head><meta charset="utf-8">
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
  if (t.getAttribute('data-action') === 'continue') post({{type:'continue_reading'}});
  else post({{type:'open_book', file: t.getAttribute('data-file')}});
}});
</script>
</head><body class="home-body">
<div class="home">
  <div class="home-title">Omarchy&#8209;Bible</div>
  <div class="home-sub">Choose a Bible translation to begin.</div>
  {ans}
  <div class="section">
    <div class="section-title">Bible Translations</div>
    <div class="book-list">{items}</div>
  </div>
  <div class="home-footer">Ctrl+H header · Ctrl+N notes · Ctrl+T contents · Ctrl+P home</div>
</div>
</body></html>"""
        self.webview.load_html(html, None)

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

    def open_book(self, path):
        self._resume_index = None
        self._resume_page_num = None
        self.show_loading()
        # Use idle callback so the loading screen renders before we block on epub.read_epub
        GLib.idle_add(self._open_book_real, path)

    def _open_book_real(self, path):
        try:
            self.book_path = path
            self.book = epub.read_epub(path)
            self._prepare_chapters()
            if not self.chapters:
                self.show_welcome()
                return False
            start = 0
            if getattr(self, "_resume_index", None) is not None:
                start = min(self._resume_index, len(self.chapters) - 1)
            self.chapter_index = start
            GLib.idle_add(self._do_load_chapter, start)
        except Exception as e:
            self.show_welcome()
            self.progress_label.set_text(f"Error: {e}")
        return False

    def _prepare_chapters(self):
        if self.bookdir:
            shutil.rmtree(self.bookdir, ignore_errors=True)
        self.bookdir = tempfile.mkdtemp(prefix="omarchy-bible-")

        self._item_paths = {}
        for item in self.book.get_items():
            name = item.get_name()
            if not name:
                continue
            safe = os.path.join(self.bookdir, name)
            os.makedirs(os.path.dirname(safe) or self.bookdir, exist_ok=True)
            try:
                with open(safe, "wb") as f:
                    f.write(item.get_content())
                self._item_paths[name] = safe
            except Exception:
                pass

        # Build chapters from the book's own table of contents (ebooklib exposes
        # it as a flat list of epub.Link objects). This gives the structural
        # chapter list (e.g. "Genesis", "Exodus", ...) rather than every spine
        # item, which for many books is hundreds of raw fragments.
        entries = self._flatten_toc()

        # Many free Bibles (e.g. these eReaderBibles EPUBs) expose their TOC
        # only through the NCX, which ebooklib does not populate into .toc.
        # Parse the NCX directly in that case.
        if not entries:
            entries = self._parse_ncx()

        self.chapters = []
        for title, href in entries:
            name = self._resolve_href(href)
            if not name:
                continue
            path = self._item_paths.get(name)
            if not path:
                continue
            self.chapters.append(("", title or "Chapter", path, name))

        # Fallback: no usable TOC, use document spine items.
        if not self.chapters:
            for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                name = item.get_name()
                path = self._item_paths.get(name)
                if not path:
                    continue
                title = getattr(item, "title", None) or os.path.basename(name)
                self.chapters.append(("", title, path, name))

    def _flatten_toc(self):
        """Return a flat list of (title, href) from the book's TOC.

        ebooklib's `book.toc` is normally a flat list of epub.Link, but some
        books nest sections as (Link, [children]) tuples. This flattens both.
        """
        result = []
        raw = getattr(self.book, "toc", None) or []
        if isinstance(raw, str):
            return result
        stack = list(raw)
        while stack:
            node = stack.pop(0)
            if isinstance(node, tuple):
                link, children = node[0], node[1]
                if isinstance(link, str) or link is None:
                    continue
                try:
                    if link.title:
                        result.append((link.title, link.href))
                except Exception:
                    pass
                if children:
                    stack = list(children) + stack
            else:
                try:
                    if getattr(node, "title", None):
                        result.append((node.title, node.href))
                except Exception:
                    pass
        return result

    def _resolve_href(self, href):
        """Resolve a TOC href to a document item name, or '' if not found."""
        if not href:
            return ""
        target = href.split("#")[0].replace("\\", "/")
        while target.startswith("./"):
            target = target[2:]
        # ebooklib item names may also carry a leading './'
        if target in self._item_paths:
            return target
        stripped = target.lstrip("./")
        for name in self._item_paths:
            if name.lstrip("./") == stripped:
                return name
        return ""

    def _parse_ncx(self):
        """Parse the book's NCX to extract (title, href) chapter entries.

        Many free Bibles (including the eReaderBibles EPUBs) keep their full
        table of contents in the NCX file, which ebooklib does not translate
        into `book.toc`. Here we walk the navMap and return the leaf navPoints
        (those without nested children), skipping the parent book-name
        containers so we end up with entries like "Genesis 1", "Genesis 2", ...
        """
        import xml.etree.ElementTree as ET

        entries = []
        ncx = None
        for item in self.book.get_items():
            if item.get_name().lower().endswith(("toc.ncx", ".ncx")):
                ncx = item
                break
        if ncx is None:
            return entries

        try:
            raw = (ncx.get_content() or b"").decode("utf-8", "replace")
            root = ET.fromstring(raw)
        except Exception:
            return entries

        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        def walk(navpoint):
            label = navpoint.find(f"{ns}navLabel/{ns}text")
            content = navpoint.find(f"{ns}content")
            children = navpoint.findall(f"{ns}navPoint")
            title = (label.text or "").strip() if label is not None else ""
            src = content.get("src", "") if content is not None else ""
            if not children and title and src:
                entries.append((title, src))
            for child in children:
                walk(child)

        navmap = root.find(f"{ns}navMap")
        if navmap is not None:
            for navpoint in navmap.findall(f"{ns}navPoint"):
                walk(navpoint)

        return entries

    def _extract_body(self, content):
        m = re.search(r"<body[^>]*>(.*?)</body>", content, re.S | re.I | re.DOTALL)
        body = m.group(1) if m else content

        # Drop anything that would inject its own colors/styles and override
        # the reader's theme: <style>, <link>, <base>, and inline style
        # attributes that set color or background.
        body = re.sub(r"<style[\s\S]*?</style>", "", body, flags=re.I)
        body = re.sub(r"<link\b[^>]*>", "", body, flags=re.I)
        body = re.sub(r"<base\b[^>]*/?>", "", body, flags=re.I)

        def neutral_style(attr):
            value = re.sub(
                r"([a-zA-Z-]*background[a-zA-Z-]*|color)\s*:\s*[^;\"']*;?",
                "",
                attr.group(1),
                flags=re.I,
            ).strip()
            if value:
                return 'style="' + value.rstrip("; ") + '"'
            return ""

        body = re.sub(r'style\s*=\s*"([^"]*)"', neutral_style, body, flags=re.I)
        body = re.sub(r"style\s*=\s*'([^']*)'", neutral_style, body, flags=re.I)
        return body

    # ---------------- Chapter loading ----------------
    def _do_load_chapter(self, index):
        if index < 0 or index >= len(self.chapters):
            return False
        self.chapter_index = index
        _, title, path, _ = self.chapters[index]
        self.title_label.set_text(title)
        self.progress_label.set_text(f"{index + 1}/{len(self.chapters)}")

        with open(path, "rb") as f:
            raw = f.read()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("latin-1", "replace")

        body_content = self._extract_body(content)
        base_url = "file://" + os.path.dirname(path) + "/"

        html = f"""<!doctype html><html><head><meta charset="utf-8">
{self._styles()}
<script>{PAGE_JS}</script>
</head><body>
<div id="source" style="display:none;">{body_content}</div>
<div id="container"></div>
</body></html>"""

        self._is_loading = True
        self._page_pages = 0
        self._show_spinner(True)
        self.webview.load_html(html, base_url)
        return False

    # ---------------- Load events ----------------
    def on_load_changed(self, webview, event):
        if event != WebKit2.LoadEvent.FINISHED:
            return
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
        elif mtype == "ready":
            self._is_loading = False
            pages = data.get("pages", 0)
            self.loading_box.set_visible(False)
            self._show_spinner(False)
            self.current_page = 0
            self.page_count = pages
            self._show_progress(1)

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
            if pages <= 0:
                # empty chapter - advance to next non-empty automatically
                self.next_chapter()
        elif mtype == "edge":
            if data.get("dir") == "next":
                self.next_chapter()
            else:
                self.prev_chapter()
        elif mtype == "page":
            self._page_pages = data.get("pages", 0)
            self.current_page = data.get("cur", 0)
            self.page_count = data.get("pages", 0)
            self._show_progress(data.get("cur", 0) + 1)
            self._save_state()
            self._refresh_notes()

    def _save_state(self):
        if not self.book_path:
            return
        filename = os.path.basename(self.book_path)
        save_state({
            "book": filename,
            "chapter": self.chapter_index + 1,
            "page": self.current_page + 1,
        })

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
        self.open_book(path)
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
        if self.chapter_index + 1 < len(self.chapters):
            self._do_load_chapter(self.chapter_index + 1)
            return True
        return False

    def prev_chapter(self):
        if self.chapter_index - 1 >= 0:
            self._do_load_chapter(self.chapter_index - 1)
            return True
        return False

    def _hotkey_matches(self, binding, keyname, state):
        """Return True when a configured binding (e.g. \"Ctrl+equal\") matches.

        Bindings are written as modifier + key name, e.g. Ctrl+t, Ctrl+H, or a
        bare key name like t. Only Ctrl is supported right now.
        """
        if not binding:
            return False
        parts = [p.strip() for p in binding.split("+")]
        want_ctrl = "ctrl" in [p.lower() for p in parts]
        key = parts[-1]
        if keyname != key:
            return False
        has_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        return has_ctrl == want_ctrl

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

    def on_key_pressed_raw(self, widget, event):
        keyname = Gdk.keyval_name(event.keyval)
        state = event.state

        if keyname == "Escape":
            if self._notes_overlay.get_visible():
                self._hide_notes()
                return True
            if self._settings_overlay.get_visible():
                self._hide_settings()
                return True
            if self.toc_overlay.get_visible():
                self._hide_toc()
                return True
            return False

        # When typing in a text field (e.g. the notes editor), do not intercept
        # plain keys (SPACE, letters, ...) — otherwise they trigger page
        # navigation. Ctrl+key hotkeys and Escape are still handled above/below.
        if self._focus_in_text_input() and not (state & Gdk.ModifierType.CONTROL_MASK):
            return False

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
        if self._hotkey_matches(HOTKEYS.get("toggle_header"), keyname, state):
            self._toggle_header()
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

        # Table of contents: arrow keys + Neo-Vim J/k navigation.
        if self.toc_overlay.get_visible():
            if keyname == "Down" or keyname == "j":
                self._toc_move(1)
                return True
            if keyname == "Up" or keyname == "k":
                self._toc_move(-1)
                return True
            if keyname == "Return" or keyname == "KP_Enter":
                self._toc_activate_current()
                return True

        if keyname == "Right":
            self._run_js("nextPage();")
            return True
        elif keyname == "Left":
            self._run_js("prevPage();")
            return True
        elif keyname == "Page_Down" or keyname == "space":
            self._run_js("nextPage();")
            return True
        elif keyname == "Page_Up":
            self._run_js("prevPage();")
            return True
        elif keyname == "o" and (state & Gdk.ModifierType.CONTROL_MASK):
            self.on_open()
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
