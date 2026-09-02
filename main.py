#!/usr/bin/env python3
"""Omarchy-Bible - a minimal EPUB Bible reader styled after the Omarchy Ash theme."""

import html
import os
import re
import sys
import threading
import zipfile
import json
import tomllib
import tempfile
import shutil
import collections

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
    "toggle_header": "Ctrl+Shift+h",
    "toggle_reader_mode": "Ctrl+b",
    "page_next": "Ctrl+Right",
    "page_prev": "Ctrl+Left",
    "note": "Ctrl+n",
    "home": "Ctrl+p",
    "home_bracket": "Ctrl+bracketleft",
    "settings": "Ctrl+s",
    "cycle_section": "Ctrl+h/j/k/l",
    "help": "Ctrl+Shift+k",
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


def log_import(msg):
    """Append a timestamped line to the import diagnostic log."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        import datetime
        with open(os.path.join(DATA_DIR, "import.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now().isoformat()} {msg}\n")
    except Exception:
        pass


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
    """Return notes dict: {location_key: [note_entry, ...]}.

    Each note entry is {"text": str, "ts": iso-timestamp}. Entries saved by
    older versions as plain strings are migrated in place to this shape.
    """
    try:
        with open(NOTES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if not isinstance(data, dict):
                return {}
    except Exception:
        return {}
    for key in list(data.keys()):
        items = data[key]
        if not isinstance(items, list):
            data[key] = []
            continue
        migrated = []
        for it in items:
            if isinstance(it, str):
                migrated.append({"text": it, "ts": ""})
            elif isinstance(it, dict):
                migrated.append(
                    {
                        "text": str(it.get("text", "")),
                        "ts": str(it.get("ts", "")),
                        "verse": int(it.get("verse") or 0),
                    }
                )
        data[key] = [n for n in migrated if n["text"].strip()]
    return data


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
    "auto_hide_notes": True,
    "note_panel_height": 300,
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
  sup.v {{
    color: {accent};
    font-weight: bold;
    font-size: 0.72em;
    margin-right: 0.35em;
  }}
  span.v {{
    color: {accent};
    font-weight: bold;
    margin-right: 0.15em;
  }}
  .verse-line {{
    margin: 0.2em 0;
    line-height: 1.55;
  }}
  .v-highlight {{
    background: alpha({accent}, 0.55) !important;
    color: {foreground} !important;
    border-radius: 3px;
    padding: 0 2px;
  }}
  .verse-glow {{
    background: alpha({accent}, 0.10) !important;
    border-left: 3px solid {accent};
    padding-left: 0.6em;
  }}
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
  .home-status {{
    background: alpha({accent}, 0.12);
    border: 1px solid {accent};
    border-radius: 8px;
    padding: 10px 14px;
    color: {foreground};
    margin-bottom: 20px;
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
  .focused {{
    outline: 2px solid {accent};
    outline-offset: 2px;
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

  var pieceLens = pieces.map(function (s) {
    var d = document.createElement('div');
    d.innerHTML = s;
    return d.textContent.length;
  });

  var pages = [];
  var charStarts = [];
  var block = '';
  var blockLen = 0;
  var total = 0;
  for (var i = 0; i < pieces.length; i++) {
    var candidate = block + pieces[i];
    if (block && measure(candidate) > pageHeight) {
      pages.push(block);
      charStarts.push(total);
      total += blockLen;
      block = pieces[i];
      blockLen = pieceLens[i];
    } else {
      block = candidate;
      blockLen += pieceLens[i];
    }
  }
  if (block) {
    pages.push(block);
    charStarts.push(total);
  }
  document.body.removeChild(probe);

  state.charStarts = charStarts;
  state.pages = pages.length;
  state.verseStarts = [];
  for (var p = 0; p < pages.length; p++) {
    var m = pages[p].match(/data-vn="(\d+)"/);
    state.verseStarts[p] = m ? parseInt(m[1], 10) : null;
  }
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

function currentCharStart(p) {
  return (state.charStarts && state.charStarts[p] != null) ? state.charStarts[p] : 0;
}

function report() {
  post({type:'ready', pages: state.pages, pch: currentCharStart(state.current)});
}

function currentPageIdx() { return state.current; }

function clearVerseHighlight() {
  var all = document.querySelectorAll('.verse-glow, .v-highlight');
  for (var i = 0; i < all.length; i++) {
    all[i].classList.remove('verse-glow');
    all[i].classList.remove('v-highlight');
  }
}

function resetVerseHighlight() {
  clearVerseHighlight();
  post({type:'verse', verse: 0});
}

function verseElems() {
  var p = document.querySelector('.page.active');
  if (!p) return [];
  return Array.prototype.slice.call(p.querySelectorAll('sup.v, span.v'));
}

function currentVerseEl() {
  var p = document.querySelector('.page.active');
  if (!p) return null;
  return p.querySelector('.v-highlight');
}

function applyVerseHighlight(el, vn) {
  clearVerseHighlight();
  if (!el) return;
  el.classList.add('v-highlight');
  var par = el.closest('p');
  if (par) par.classList.add('verse-glow');
  var page = el.closest('.page');
  if (page && page.scrollHeight > page.clientHeight) {
    page.scrollTop = Math.max(0, el.offsetTop - page.clientHeight * 0.35);
  }
  if (vn) post({type:'verse', verse: parseInt(vn, 10) || 0});
}

function moveVerse(delta) {
  var els = verseElems();
  if (els.length === 0) {
    scrollContent(delta > 0 ? 70 : -70);
    return false;
  }
  var cur = currentVerseEl();
  var idx = 0;
  if (cur) {
    var found = Array.prototype.indexOf.call(els, cur);
    if (found >= 0) idx = found + delta;
  }
  if (idx < 0) idx = 0;
  if (idx >= els.length) idx = els.length - 1;
  var el = els[idx];
  applyVerseHighlight(el, el.getAttribute('data-vn'));
  return true;
}

// Handle reader navigation directly in the page so it works even when the
// GTK key-press-event path misses the event.
document.addEventListener('keydown', function (e) {
  var tag = e.target.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  var key = e.key.toLowerCase();
  if (key === 'j' || key === 'arrowdown') {
    e.preventDefault();
    moveVerse(1);
    return;
  }
  if (key === 'k' || key === 'arrowup') {
    e.preventDefault();
    moveVerse(-1);
    return;
  }
  if (key === 'l' || key === 'arrowright') {
    e.preventDefault();
    nextPage();
    return;
  }
  if (key === 'h' || key === 'arrowleft') {
    e.preventDefault();
    prevPage();
    return;
  }
});

// Intercept clicks on reference/commentary markers (<a> with a title or an
// in-book #fragment) and send the note text to the Python panel instead of
// letting the webview navigate away.
document.addEventListener('click', function (e) {
  if (!e.target.closest) return;
  var a = e.target.closest('a[href]');
  if (!a) return;
  var href = a.getAttribute('href') || '';
  var title = a.getAttribute('title') || '';
  if (!title && href.indexOf('#') === -1) return;
  e.preventDefault();
  var label = (a.textContent || '').trim();
  var text = title;
  if (!text && href.indexOf('#') !== -1) {
    var frag = href.split('#').pop();
    try { frag = decodeURIComponent(frag); } catch (err) {}
    var t = document.getElementById(frag);
    if (t) {
      var box = t.closest ? (t.closest('p,li,div,blockquote') || t.parentNode) : t.parentNode;
      text = ((box || t).textContent || '').trim();
    }
  }
  post({type: 'ref', label: label, text: text});
});

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
  resetVerseHighlight();
  post({type:'page', cur: idx, pages: state.pages, pch: currentCharStart(idx)});
  return idx;
}

function scrollContent(delta) {
  var el = document.querySelector('.page.active');
  var cont = document.scrollingElement || document.documentElement;
  if (el && el.scrollHeight > el.clientHeight) {
    el.scrollTop += delta;
  } else if (cont) {
    cont.scrollTop += delta;
  }
}

function goToVerse(vn) {
  if (!state.ready || state.pages === 0) return false;
  vn = parseInt(vn, 10) || 0;
  if (vn <= 0) return false;
  var starts = state.verseStarts || [];
  var pageIdx = 0;
  for (var i = 0; i < starts.length; i++) {
    if (starts[i] != null && starts[i] <= vn) pageIdx = i;
  }
  var pages = document.querySelectorAll('.page');
  if (pages.length === 0) return false;
  for (var i = 0; i < pages.length; i++) {
    pages[i].className = i === pageIdx ? 'page active' : 'page';
  }
  state.current = pageIdx;
  window.scrollTo(0, 0);
  post({type:'page', cur: pageIdx, pages: state.pages, pch: currentCharStart(pageIdx)});
  var els = verseElems();
  var found = null;
  for (var i = 0; i < els.length; i++) {
    if (parseInt(els[i].getAttribute('data-vn'), 10) === vn) {
      found = els[i];
      break;
    }
  }
  if (found) {
    applyVerseHighlight(found, vn);
  } else {
    clearVerseHighlight();
    post({type:'verse', verse: 0});
  }
  return true;
}

function nextPage() {
  if (!state.ready) return -2;
  if (state.pages === 0) { post({type:'edge', dir:'next'}); return -1; }
  if (state.current + 1 < state.pages) {
    return showPage(state.current + 1);
  }
  post({type:'edge', dir:'next'});
  return -1;
}

function prevPage() {
  if (!state.ready) return -2;
  if (state.pages === 0) { post({type:'edge', dir:'prev'}); return -1; }
  if (state.current - 1 >= 0) {
    return showPage(state.current - 1);
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
        self._chapter_verses = {}
        self._chapter_refs = {}
        self._chapter_slice = {}
        self._item_text_cache = {}
        self._refs_overlay = None
        self._refs_pinned = None
        self.chapter_index = 0
        self.current_page = 0
        self.current_ch = 0
        self.page_count = 0
        self.font_size = 18
        self.reading_mode = "dark"
        self._toc_books = []
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
        self._current_verse = 0
        self._on_home = False
        self._home_options = []
        self._home_sel = 0

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
            verse_list = self._chapter_verses.get(chapter["index"], [])
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
        for bi, book in enumerate(self._toc_books):
            for ci, chapter in enumerate(book["chapters"]):
                if chapter["index"] == self.chapter_index:
                    return bi, ci
        return 0, 0

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
            self.toc_list.select_row(self._toc_rows[self._toc_book_idx])
            self.toc_list.grab_focus()

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
        self.toc_list.select_row(self._toc_rows[target])
        self.toc_list.scroll_to_row(self._toc_rows[target])

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
            self._fill_verse_numbers(chapter_index)
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
        new = Gtk.Button(label="New")
        new.connect("clicked", self._on_note_new)
        add_row.pack_start(new, False, False, 0)
        add = Gtk.Button(label="Add")
        add.connect("clicked", self._on_note_add)
        add_row.pack_start(add, False, False, 0)

        hint = Gtk.Label(label="Ctrl+Enter to save \u00b7 Ctrl+N to close")
        hint.get_style_context().add_class("progress-label")
        add_row.pack_start(hint, True, True, 0)
        side.pack_start(add_row, False, False, 0)

        body.pack_start(side, False, False, 0)

        self._notes_overlay.pack_start(bar, False, False, 0)
        self._notes_overlay.pack_start(body, True, True, 0)
        self._apply_note_height(SETTINGS.get("note_panel_height", 300))

    def _build_refs_overlay(self):
        """Build the reference/commentary panel (a strip above the notes)."""
        self._refs_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._refs_overlay.set_visible(False)
        self._refs_overlay.set_halign(Gtk.Align.FILL)
        self._refs_overlay.set_valign(Gtk.Align.END)
        self._refs_overlay.set_size_request(-1, 240)
        self._refs_overlay.get_style_context().add_class("refs-overlay")

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        bar.set_margin_top(10)
        bar.set_margin_bottom(6)

        title = Gtk.Label(label="References")
        title.get_style_context().add_class("title-label")
        title.set_halign(Gtk.Align.START)
        bar.pack_start(title, True, True, 0)

        self.refs_loc = Gtk.Label(label="")
        self.refs_loc.get_style_context().add_class("progress-label")
        bar.pack_end(self.refs_loc, False, False, 0)

        close = Gtk.Button(label="\u2715")
        close.connect("clicked", lambda *_: self._hide_refs())
        bar.pack_end(close, False, False, 0)

        self._refs_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._refs_body.set_margin_start(16)
        self._refs_body.set_margin_end(16)
        self._refs_body.set_margin_bottom(12)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.add(self._refs_body)
        scroller.get_style_context().add_class("refs-scroller")

        self._refs_overlay.pack_start(bar, False, False, 0)
        self._refs_overlay.pack_start(scroller, True, True, 0)

    def _show_refs(self):
        if not self.book_path or not self.chapters:
            return
        self._refs_overlay.set_visible(True)
        self._position_refs_above_notes()
        self._refresh_refs()

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

    def _refresh_refs(self):
        """Re-render the reference panel for the currently highlighted verse."""
        if self._refs_overlay is None or not self._refs_overlay.get_visible():
            return
        # Compute per-verse references lazily the first time the panel is shown
        # for a chapter (this can read the study-notes / crossrefs companions).
        if self.chapter_index not in self._chapter_refs:
            self._ensure_chapter_refs(self.chapter_index)
        for child in self._refs_body.get_children():
            self._refs_body.remove(child)

        chapter_refs = self._chapter_refs.get(self.chapter_index, {})
        verse = getattr(self, "_current_verse", 0)

        if verse:
            self.refs_loc.set_text(f"v. {verse}")
        else:
            self.refs_loc.set_text("")

        entries = []
        if self._refs_pinned is not None:
            entries = [self._refs_pinned]
        elif verse:
            entries = chapter_refs.get(verse, [])

        if not entries:
            placeholder = Gtk.Label(
                label="No references for this verse."
                if verse
                else "Select a verse with j/k to see its references."
            )
            placeholder.get_style_context().add_class("progress-label")
            placeholder.set_halign(Gtk.Align.START)
            self._refs_body.pack_start(placeholder, False, False, 0)
        else:
            for e in entries:
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                card.get_style_context().add_class("ref-card")
                label = e.get("label", "")
                head = Gtk.Label(label=f"[{label}] " if label else "")
                head.get_style_context().add_class("toc-current")
                head.set_halign(Gtk.Align.START)
                text = Gtk.Label(label=e.get("text", ""))
                text.set_halign(Gtk.Align.START)
                text.set_xalign(0.0)
                text.set_line_wrap(True)
                text.set_selectable(True)
                if label:
                    card.pack_start(head, False, False, 0)
                card.pack_start(text, False, False, 0)
                self._refs_body.pack_start(card, False, False, 0)

        self._refs_overlay.show_all()
        self._refs_overlay.set_visible(True)
        self._position_refs_above_notes()

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
        """Stable location key for the current book/chapter.

        Uses the character offset into the chapter where the current page
        starts (reported by the paginator), so notes stay anchored to the
        text rather than to a page number that shifts with window size.
        """
        if not self.book_path or not self.chapters:
            return None
        book = os.path.basename(self.book_path)
        return f"{book}|{self.chapter_index}|{self.current_ch}"

    def _legacy_note_keys(self):
        """Older notes.json entries anchored by page number instead of char."""
        if not self.book_path or not self.chapters:
            return []
        book = os.path.basename(self.book_path)
        return [f"{book}|{self.chapter_index}|{self.current_page}"]

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
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and (
            event.state & Gdk.ModifierType.CONTROL_MASK
        ):
            self._add_note()
            return True
        return False

    def _on_note_add(self, button):
        self._add_note()

    def _on_note_new(self, button):
        self._editing_note = None
        self.notes_buffer.set_text("")
        self.notes_textview.grab_focus()

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
            ("Ctrl + N", "Notes panel (New button / Ctrl+Enter to add)"),
            ("Ctrl + R", "Reference / commentary panel (shown above notes)"),
            ("Ctrl + h", "Jump to notes list (works while typing)"),
            ("Ctrl + l", "Add a note / edit the highlighted note"),
            ("Ctrl + j / k", "Notes list: move highlight · elsewhere: cycle sections"),
            ("Ctrl + Shift + H", "Toggle header bar"),
            ("Ctrl + S", "Settings"),
            ("Ctrl + Shift + K", "Keybindings reference"),
            ("Ctrl + [ / Ctrl + P", "Home / choose a translation"),
            ("Ctrl + B", "Toggle reader mode"),
            ("Ctrl + O", "Open a book file"),
            ("H / L / ← / →", "Previous / next page (left / right)"),
            ("J / K / ↑ / ↓", "Notes highlighted: move up / down · content: step verses (j/↓ down, k/↑ up)"),
            ("Home: j/k", "Select Continue where you left off / Translations, Enter"),
            ("x", "Delete the highlighted note"),
            ("Ctrl + Shift + +/-", "Grow / shrink note panel"),
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
   <div class="home-footer">j/k select · Enter open · Ctrl+[ home · Ctrl+Shift+K keys</div>
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
                msg = f"Imported {name}. Select it above to open."
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
        self._chapter_verses = {}
        self._chapter_refs = {}
        self._chapter_slice = {}
        self._item_text_cache = {}
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

        # Dedicated handling for Crossway "ESV Study Bible" style EPUBs: scripture
        # lives in bNN.MM.Book.text.html files with anchor-based chapters
        # (<span class="chapter-num">), per-verse <span class="verse-num">, and
        # separate .studynotes.html / .crossrefs.html companions.
        if self._looks_like_study_bible() and self._prepare_study_bible():
            return

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

        # Some publishers (e.g. Crossway ESV) list only book-level entries in
        # the TOC; the book file itself contains links to the chapter files.
        # Expand those entries so each chapter becomes its own item.
        expanded = []
        for title, href in entries:
            intro_name = self._resolve_href(href)
            intro_path = self._item_paths.get(intro_name)
            if intro_path:
                links = self._extract_chapter_links(intro_path)
                if links:
                    for link_text, ch_href in links:
                        ch_name = self._resolve_href(ch_href)
                        ch_path = self._item_paths.get(ch_name)
                        if ch_path:
                            expanded.append((title, f"{title} {link_text}", ch_path, ch_name))
                    continue
            # No chapter links: treat this entry as a chapter itself.
            if intro_path:
                expanded.append((title, title or "Chapter", intro_path, intro_name))

        # Fallback: no usable TOC, use document spine items.
        if not expanded:
            for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                name = item.get_name()
                path = self._item_paths.get(name)
                if not path:
                    continue
                title = getattr(item, "title", None) or os.path.basename(name)
                expanded.append((title, title, path, name))

        # Group chapters into books. For Crossway, book_title is already the
        # book name. For flat chapter lists like KJV/ASV, book_title is the
        # same as chapter_title; re-group by removing the trailing chapter
        # number from the title.
        books_map = {}
        for book_title, ch_title, path, name in expanded:
            if book_title not in books_map:
                books_map[book_title] = []
            books_map[book_title].append((ch_title, path, name))

        if all(len(chs) == 1 for chs in books_map.values()):
            books_map = {}
            for book_title, ch_title, path, name in expanded:
                m = re.match(r"^(.*?)\s+(\d+)$", (ch_title or "").strip())
                bt = m.group(1).strip() if m else book_title
                books_map.setdefault(bt, []).append((ch_title, path, name))

        self.chapters = []
        self._toc_books = []
        for book_title, chs in books_map.items():
            book_chapters = []
            for ch_title, path, name in chs:
                idx = len(self.chapters)
                self.chapters.append(("", ch_title, path, name))
                book_chapters.append({"title": ch_title, "index": idx, "verses": []})
            self._toc_books.append({"title": book_title, "chapters": book_chapters})

    def _looks_like_study_bible(self):
        """True if the book has Crossway Study-Bible style bNN.MM.*.text.html."""
        for name in self._item_paths:
            if re.search(r'(?:^|/)b\d{2}\.\d{2}\..+\.text\.html$', name):
                return True
        return False

    def _prepare_study_bible(self):
        """Build chapters for a Crossway ESV Study Bible by slicing text files.

        Chapters are anchor-based: each book's scripture lives in one or more
        bNN.MM.Book.text.html files, and each chapter begins at a
        '<span class="chapter-num">N</span>' preceded by the section heading
        '<p id="vNN NNN 001">'. We record per-chapter (start, end) offsets into
        the file so a chapter can be rendered on its own.
        """
        by_book = collections.OrderedDict()
        for name, path in self._item_paths.items():
            m = re.search(r'(?:^|/)b(\d{2})\.(\d{2})\.(.+?)\.text\.html$', name)
            if m:
                by_book.setdefault(m.group(1), []).append(
                    (int(m.group(2)), name, path, m.group(3))
                )
        if not by_book:
            return False

        self.chapters = []
        self._toc_books = []
        self._chapter_slice = {}
        for bid in sorted(by_book):
            book_name = None
            book_chapters = []
            for _part, name, path, raw_name in sorted(by_book[bid]):
                try:
                    with open(path, "rb") as f:
                        content = f.read().decode("utf-8", "replace")
                except Exception:
                    continue
                if not book_name:
                    h = re.search(r"<h2>(.*?)</h2>", content, flags=re.S)
                    book_name = re.sub(r"<[^>]+>", "", h.group(1)).strip() if h else raw_name
                marks = list(
                    re.finditer(r'<span class="chapter-num">\s*(\d+)\s*</span>', content)
                )
                bounds = []
                for mk in marks:
                    cnum = int(mk.group(1))
                    heading_id = f"v{bid}{cnum:03d}001"
                    hp = content.find(f'id="{heading_id}"')
                    start = mk.start()
                    if hp != -1 and hp < mk.start():
                        pstart = content.rfind("<p", 0, hp)
                        if pstart != -1:
                            start = pstart
                    bounds.append((cnum, start))
                for k, (cnum, start) in enumerate(bounds):
                    if k + 1 < len(bounds):
                        end = bounds[k + 1][1]
                    else:
                        be = content.find("</body>", start)
                        end = be if be != -1 else len(content)
                    idx = len(self.chapters)
                    title = f"{book_name} {cnum}"
                    self.chapters.append((book_name, title, path, name))
                    self._chapter_slice[idx] = (start, end)
                    book_chapters.append({"title": title, "index": idx, "verses": []})
            if book_chapters:
                self._toc_books.append(
                    {"title": book_name or f"Book {int(bid)}", "chapters": book_chapters}
                )
        return bool(self.chapters)

    def _extract_chapter_links(self, intro_path):
        """Extract chapter file links from a book introduction page.

        Used by Crossway-style EPUBs where the TOC lists only book-level files,
        and the book file contains links like 'Chapter 1', 'Chapter 2', ...
        """
        try:
            with open(intro_path, "rb") as f:
                content = f.read().decode("utf-8", "replace")
        except Exception:
            return []
        body = self._extract_body(content)
        links = re.findall(
            r'<a\s+[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            body,
            flags=re.I | re.S,
        )
        result = []
        for href, text in links:
            text = re.sub(r"<[^>]+>", "", text)
            text = text.replace("&nbsp;", " ").replace("\xa0", " ").strip()
            if not text:
                continue
            if not re.search(r"\bchapter\b|\bch\b", text, flags=re.I):
                continue
            ch_name = self._resolve_href(href)
            if not ch_name or ch_name not in self._item_paths:
                continue
            result.append((text, href))
        return result

    def _chapter_body(self, chapter_index):
        """Return the annotated (data-vn tagged) body HTML for one chapter.

        For anchor-sliced study Bibles this returns just that chapter's slice;
        for normal EPUBs it returns the whole chapter file's body.
        """
        if not 0 <= chapter_index < len(self.chapters):
            return ""
        _, _, path, _ = self.chapters[chapter_index]
        try:
            with open(path, "rb") as f:
                content = f.read().decode("utf-8", "replace")
        except Exception:
            return ""
        sl = self._chapter_slice.get(chapter_index)
        if sl:
            content = content[sl[0]:sl[1]]
        body = self._annotate_verses(self._extract_body(content))
        return self._inject_first_verse(body)

    def _inject_first_verse(self, body):
        """Tag the implicit verse 1 in study Bibles where only the chapter number
        appears at the start of a chapter (no explicit '1' marker)."""
        if re.search(r'data-vn="1"', body):
            return body
        return re.sub(
            r'(<span class="chapter-num">\s*\d+\s*</span>)',
            r'\1<span class="v" data-vn="1">1 </span>',
            body,
            count=1,
            flags=re.I,
        )

    def _fill_verse_numbers(self, chapter_index):
        """Populate the verse list for a chapter (fast; refs are lazy)."""
        if chapter_index in self._chapter_verses:
            return
        if not 0 <= chapter_index < len(self.chapters):
            return
        body = self._chapter_body(chapter_index)
        numbers = sorted({int(n) for n in re.findall(r'data-vn="(\d+)"', body)})
        self._chapter_verses[chapter_index] = numbers

    def _ensure_chapter_refs(self, chapter_index):
        """Lazily compute the per-verse reference/commentary map for a chapter."""
        if chapter_index in self._chapter_refs:
            return
        if not 0 <= chapter_index < len(self.chapters):
            self._chapter_refs[chapter_index] = {}
            return
        _, _, path, _ = self.chapters[chapter_index]
        body = self._chapter_body(chapter_index)
        self._chapter_refs[chapter_index] = self._extract_refs(body, os.path.dirname(path))

    def _extract_refs(self, body, chapter_dir):
        """Build {verse_number: [{label, text}]} for footnote/commentary links.

        Handles the common study-Bible marker styles: an <a> carrying an inline
        title="..." note, an <a> linking to an id in the same chapter file, and
        an <a> linking to an id in another extracted file (footnote appendix).
        """
        anchor_re = re.compile(
            r'data-vn="(\d+)"|<a\b([^>]*?)>(.*?)</a>', re.I | re.S
        )
        refs = {}
        current = None

        def attr(attrs, name):
            m = re.search(name + r'="([^"]*)"', attrs or "", re.I)
            return m.group(1) if m else ""

        def clean(seg):
            seg = re.sub(r"<[^>]+>", " ", seg)
            seg = html.unescape(seg)
            return re.sub(r"\s+", " ", seg).strip()

        def text_from(src, want_id):
            m = re.search(r'id="' + re.escape(want_id) + r'"', src)
            if not m:
                return ""
            pos = m.end()
            gt = src.find(">", pos)
            body_from = (gt + 1) if gt != -1 and gt < pos + 400 else pos
            # Crossref entry: just this letter's refs until the next letter/verse.
            if re.match(r"c\d+\.\w", want_id):
                seg = src[body_from:body_from + 1200]
                stop = re.search(r'<span class="crossref-|</p>|<h\d', seg)
                if stop:
                    seg = seg[:stop.start()]
                return clean(seg)
            # Study note / generic: capture the enclosing block element.
            block_start = max(
                src.rfind("<p", 0, m.start()),
                src.rfind("<div", 0, m.start()),
                src.rfind("<li", 0, m.start()),
                src.rfind("<blockquote", 0, m.start()),
            )
            if block_start == -1:
                block_start = m.start()
            tagm = re.match(r"<(\w+)", src[block_start:])
            tag = tagm.group(1) if tagm else "p"
            close = src.find("</" + tag + ">", body_from)
            if close == -1:
                block_end = body_from + 2500
            else:
                block_end = close
            seg = src[block_start:block_end]
            return clean(seg)[:2500]

        def resolve_text(href, title):
            if title:
                return html.unescape(title).strip()
            if not href or href.startswith("http") or href.startswith("mailto"):
                return ""
            file_part, _, frag = href.partition("#")
            if not frag:
                return ""
            if not file_part:
                return text_from(body, frag)
            fname = os.path.normpath(os.path.join(chapter_dir, file_part))
            src = self._item_text_cache.get(fname)
            if src is None:
                try:
                    with open(fname, "rb") as f:
                        src = f.read().decode("utf-8", "replace")
                except Exception:
                    src = ""
                self._item_text_cache[fname] = src
            return text_from(src, frag)

        for m in anchor_re.finditer(body):
            if m.group(1) is not None:
                current = int(m.group(1))
                continue
            attrs, inner = m.group(2), m.group(3)
            label = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
            title = attr(attrs, "title")
            href = attr(attrs, "href")
            if current is None:
                continue
            if not title and not (href and "#" in href):
                continue
            text = resolve_text(href, title)
            if not text:
                continue
            entry = {"label": label, "text": text}
            lst = refs.setdefault(current, [])
            if not any(x["label"] == label and x["text"] == text for x in lst):
                lst.append(entry)
        return refs


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

    def _annotate_verses(self, body):
        """Tag verse-number markers with data-vn so J/K can step per verse.

        Handles several publisher styles: plain <sup>N</sup> (KJV/ASV),
        <span class="bold">N </span> (ESV/EPUB), <span class="versenum">N</span>
        (Crossway), and other class-based verse markers. Only the style that
        actually appears is used.
        """
        # Plain <sup> numbers (KJV/ASV, many public-domain Bibles).
        body = re.sub(
            r"<sup[^>]*>(\d+)</sup>",
            r'<sup class="v" data-vn="\1">\1</sup>',
            body,
            flags=re.I,
        )
        def _tag_verse_span(match):
            inner = match.group(1)
            text = re.sub(r"<[^>]+>", "", inner)
            m = re.search(r"\d+", text)
            if not m:
                return match.group(0)
            vn = m.group(0)
            return f'<span class="v" data-vn="{vn}">{vn} </span>'

        if len(re.findall(r'class="v"', body)) < 3:
            # Known class-based verse markers (Crossway/ESV often use
            # class="versenum" or class="bold"). Allow nested tags like
            # <span class="bold"><big>1</big>:1 </span>.
            body = re.sub(
                r'<span\b[^>]*class="[^"]*(?:versenum|verse-num|verse|v|bold)[^"]*"[^>]*>(.*?)</span>',
                _tag_verse_span,
                body,
                flags=re.I | re.S,
            )
        if len(re.findall(r'class="v"', body)) < 3:
            # Generic span fallback: tag plain numeric spans only if there are
            # enough of them to look like verse numbers.
            candidates = re.findall(
                r'<span\b[^>]*>(.*?)</span>', body, flags=re.I | re.S
            )
            numbers = [re.search(r"\d+", c) for c in candidates]
            if len([n for n in numbers if n]) >= 3:
                body = re.sub(
                    r'<span\b[^>]*>(.*?)</span>',
                    _tag_verse_span,
                    body,
                    flags=re.I | re.S,
                )
        return body

    def _split_verses_into_lines(self, body):
        """Split paragraphs that contain multiple verses so each verse is on
        its own line. This makes j/k verse navigation feel like one line per
        verse, especially for publisher EPUBs that pack many verses into a
        single paragraph.
        """
        verse_span_pat = r'<span class="v" data-vn="(\d+)">\d+ </span>'

        def split_paragraph(match):
            p_open = match.group(1)
            p_content = match.group(2)
            markers = list(re.finditer(verse_span_pat, p_content))
            if len(markers) < 2:
                return match.group(0)
            cls_match = re.search(r'class="([^"]*)"', p_open)
            p_cls = cls_match.group(1) if cls_match else ""

            # Split the content by verse markers; keep any leading text before
            # the first marker attached to the first verse.
            chunks = re.split(verse_span_pat, p_content)
            lines = []
            for i in range(1, len(chunks), 2):
                vn = chunks[i]
                text = chunks[i + 1] if i + 1 < len(chunks) else ""
                if i == 1:
                    text = chunks[0] + text
                text = text.strip()
                if not text:
                    continue
                lines.append(
                    f'<p class="verse-line {p_cls}">'
                    f'<span class="v" data-vn="{vn}">{vn} </span>{text}</p>'
                )
            return "\n".join(lines) if lines else match.group(0)

        return re.sub(
            r'(<p\b[^>]*>)(.*?)(</p>)',
            split_paragraph,
            body,
            flags=re.I | re.S,
        )

    # ---------------- Chapter loading ----------------
    def _do_load_chapter(self, index):
        if index < 0 or index >= len(self.chapters):
            return False
        self.chapter_index = index
        _, title, path, _ = self.chapters[index]
        self.title_label.set_text(title)
        self.progress_label.set_text(f"{index + 1}/{len(self.chapters)}")

        body_content = self._split_verses_into_lines(self._chapter_body(index))
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
        self._fill_verse_numbers(index)
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
            self._refs_pinned = {
                "label": data.get("label", ""),
                "text": data.get("text", ""),
            }
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
            if shift and kn == "k":
                self._toggle_help()
                return True
            if shift and kn == "h":
                self._toggle_header()
                return True
            if shift and keyname in ("plus", "equal"):
                self._grow_note_height()
                return True
            if shift and keyname in ("minus", "underscore"):
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
                # Inside the notes list, j/k move the highlight (wrapping at
                # the ends); elsewhere they cycle forward through sections.
                if self._notes_zone == "list" and self._note_card_rows:
                    self._move_highlight(1 if kn == "j" else -1)
                else:
                    self._cycle_section()
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

        # Home screen: j/k select between "Continue where you left off" and
        # "Translations"; Enter activates the selection.
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

        # While typing in the notes editor, let plain keys type — do not let
        # H/L/J/K/x etc. trigger hotkeys (limit hotkeys while typing). Ctrl+
        # combos and Escape were already handled above.
        if self._focus_in_text_input():
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
