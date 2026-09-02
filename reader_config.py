"""Config, theming, persistence and small EPUB library helpers."""
import json
import os
import re
import tomllib


APP_ID = "org.omarchy.Bible"

# Where this project lives (used to locate bundled translations).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRANSLATIONS_DIR = os.path.join(BASE_DIR, "translations")

# User data dir for state + notes.
DATA_DIR = os.path.expanduser("~/.config/omarchy-bible")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
NOTES_PATH = os.path.join(DATA_DIR, "notes.json")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
PRAYERS_PATH = os.path.join(DATA_DIR, "prayers.json")
MEMORY_PATH = os.path.join(DATA_DIR, "memory.json")

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
    "refs": "Ctrl+r",
    "import": "Ctrl+i",
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
    # mutate in place so importers sharing the dict see updates
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
    HOTKEYS.clear()
    HOTKEYS.update(merged)
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


def _load_list(path):
    """Load a JSON file that holds a list; return [] on any problem."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_list(path, items):
    _ensure_data_dir()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(items, fh, indent=2)
    except Exception:
        pass


def load_prayers():
    return _load_list(PRAYERS_PATH)


def save_prayers(items):
    _save_list(PRAYERS_PATH, items)


def load_memory():
    return _load_list(MEMORY_PATH)


def save_memory(items):
    _save_list(MEMORY_PATH, items)


# ---------------- Settings persistence ----------------

DEFAULT_SETTINGS = {
    "auto_hide_header": True,
    "auto_hide_notes": True,
    "note_panel_height": 300,
    "show_personal_space": True,
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
