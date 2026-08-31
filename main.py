#!/usr/bin/env python3
"""Omarchy Reader - a minimal EPUB e-reader styled after the Omarchy Ash theme."""

import os
import re
import sys
import json
import tempfile
import shutil

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")

from gi.repository import Gtk, Gio, GLib, Gdk, WebKit2
from ebooklib import epub

APP_ID = "org.omarchy.Reader"

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


def current_theme_name():
    try:
        with open(os.path.join(OMARCHY_STATE, "current", "theme.name"), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return "Unknown"


FONT_FAMILY = "JetBrainsMono Nerd Font"

STYLESHEET = """
<style>
  :root {{
    color-scheme: dark;
    --bg: {background};
    --fg: {foreground};
    --muted: {muted};
    --accent: {accent};
  }}
  html, body {{
    margin: 0;
    padding: 0;
    background: transparent;
    color: var(--fg);
    font-family: {font_family}, "JetBrains Mono", monospace;
    font-size: {font_size}px;
    line-height: 1.7;
    overflow: hidden;
    height: 100%;
  }}
  body {{
    box-sizing: border-box;
    padding: 0 {side_padding}px;
  }}
  h1, h2, h3, h4, h5, h6 {{
    color: var(--accent);
    line-height: 1.3;
    margin: 1.4em 0 0.6em;
  }}
  p {{ margin: 0 0 1.1em; }}
  a {{ color: var(--fg); text-decoration: underline; }}
  blockquote {{
    border-left: 3px solid var(--accent);
    margin: 1em 0;
    padding-left: 1em;
    color: var(--muted);
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
        self.font_size = 18
        self.window = None
        self.webview = None
        self._is_loading = False
        self._theme_monitor = None
        self._parent_monitor = None
        self._monitor_parent_path = None

    def do_command_line(self, command_line):
        options = command_line.get_arguments()
        self.cli_path = options[1] if len(options) > 1 else None
        self.activate()
        return 0

    def do_activate(self):
        load_theme()
        self.create_window()
        self.window.present()
        self._start_theme_monitor()
        path = getattr(self, "cli_path", None)
        if path:
            self.open_book(path)

    def create_window(self):
        win = Gtk.ApplicationWindow(application=self)
        win.set_title("Omarchy Reader")
        win.set_default_size(900, 700)

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

        win.set_titlebar(hb)

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
        self.webview.connect("context-menu", self._suppress_menu)
        self.webview.connect("load-changed", self.on_load_changed)
        self._apply_webview_bg()

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

        self.window = win
        win.add(self.loading_overlay_win)
        win.connect("key-press-event", self.on_key_pressed_raw)

        self.show_welcome()
        self.window.show_all()

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
        color = Gdk.RGBA()
        color.parse(THEME["background"])
        self.webview.set_background_color(color)

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
        # Re-render the current view so colors are picked up live.
        if self.chapters:
            self._do_load_chapter(self.chapter_index)
        else:
            self.show_welcome()
        return False

    def _title_label(self):
        self.title_label = Gtk.Label(label="Omarchy Reader")
        self.title_label.get_style_context().add_class("title-label")
        return self.title_label

    def _fs_button(self, text, target):
        btn = Gtk.Button(label=text)
        btn.connect("clicked", lambda *_: self.change_font_size(target))
        return btn

    def _suppress_menu(self, webview, context_menu, event, hit):
        return True

    def _styles(self, top_padding=60, side_padding=64, bottom_padding=80):
        return STYLESHEET.format(
            background=THEME["background"],
            foreground=THEME["foreground"],
            muted=THEME["color11"],
            accent=THEME["accent"],
            selection_background=THEME["selection_background"],
            selection_foreground=THEME["selection_foreground"],
            font_family=FONT_FAMILY,
            font_size=self.font_size,
            top_padding=top_padding,
            side_padding=side_padding,
            bottom_padding=bottom_padding,
        )

    def show_welcome(self):
        self._is_loading = False
        self._show_spinner(False)
        self.loading_box.set_visible(False)
        html = f"""<!doctype html><html><head><meta charset="utf-8">
{self._styles()}
</head><body>
<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;text-align:center;color:{THEME['muted']};">
    <div style="font-size:{self.font_size*1.6}px;margin-bottom:0.5em;color:{THEME['accent']};">Omarchy Reader</div>
    <div style="opacity:0.8;">Open an EPUB to begin reading.</div>
    <div style="margin-top:1.5em;font-size:{self.font_size}px;">Use <b>Open Book</b> in the header, or Ctrl+O.</div>
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
            self.chapter_index = 0
            GLib.idle_add(self._do_load_chapter, 0)
        except Exception as e:
            self.show_welcome()
            self.progress_label.set_text(f"Error: {e}")
        return False

    def _prepare_chapters(self):
        if self.bookdir:
            shutil.rmtree(self.bookdir, ignore_errors=True)
        self.bookdir = tempfile.mkdtemp(prefix="omarchy-reader-")

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

        self.chapters = []
        spine = list(self.book.spine)
        for item in spine:
            try:
                idref = item[0]
            except Exception:
                continue
            chapter = self.book.get_item_with_id(idref)
            if chapter is None:
                continue
            name = chapter.get_name()
            path = self._item_paths.get(name)
            if not path:
                continue
            title = self._chapter_title(chapter)
            self.chapters.append((idref, title, path, name))

        if not self.chapters:
            for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                name = item.get_name()
                path = self._item_paths.get(name)
                if not path:
                    continue
                title = item.get_title() or os.path.basename(name)
                self.chapters.append(("", title, path, name))

    def _chapter_title(self, chapter):
        try:
            t = chapter.get_title()
            if t:
                return t
            text = chapter.get_content().decode("utf-8", "ignore")
            m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", text, re.S | re.I)
            if m:
                return re.sub(r"<[^>]+>", "", m.group(1)).strip()[:60]
        except Exception:
            pass
        return "Chapter"

    def _extract_body(self, content):
        m = re.search(r"<body[^>]*>(.*?)</body>", content, re.S | re.I | re.DOTALL)
        if m:
            return m.group(1)
        return content

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
        if mtype == "ready":
            self._is_loading = False
            pages = data.get("pages", 0)
            self.loading_box.set_visible(False)
            self._show_spinner(False)
            self._show_progress(1)
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
            self._show_progress(data.get("cur", 0) + 1)

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

    def on_key_pressed_raw(self, widget, event):
        keyname = Gdk.keyval_name(event.keyval)
        state = event.state
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

    def change_font_size(self, value):
        self.font_size = max(12, min(34, value))
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
        print(f"Omarchy Reader failed to start:\n{e}", file=sys.stderr)
        sys.exit(1)
