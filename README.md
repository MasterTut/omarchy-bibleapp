# Omarchy-Bible

A minimal, beautiful EPUB e-reader/bible for Linux, styled after the **Omarchy Ash
theme**.

Built with **GTK3 + WebKitGTK (WebKit2 4.1)** and **Python 3**, rendering each
book chapter as paginated HTML in an embedded WebView, so you get faithful
typography and layout while staying true to the Omarchy look.

## Features

- Bible translations browser (replaces recently opened)
- Continue reading (last viewed book and page)
- Page-specific notes (saved to JSON, bottom panel); a highlighted note can be
  loaded into the editor with `Ctrl + l` and saved in place; a New button always
  lets you start a fresh note
- Notes show the referenced verse or `General notes` next to the timestamp
- Settings panel (`Ctrl + S`) with auto-hide top bar toggle (persisted to `settings.json`)
- Toggleable header (`Ctrl + Shift + H`)
- Table of contents (`Ctrl + T`) navigable with arrow keys and Neo-Vim `j`/`k`
- Paginated reading (page breaks fit the viewport, no scrolling the text)
- Verse-by-verse navigation in the reader (`j` / `k` highlight each verse and
  scroll it into view)
- Notes record the highlighted verse next to the timestamp (`… · v. 5`);
  with no verse highlighted they show `General notes`
- Home screen selectable with the keyboard (`j` / `k` + Enter)
- Keyboard navigation: `←`/`→`, `PageUp`/`PageDown`, `Space`
- Auto-advance across chapters (including skipping empty pages)
- Font size controls (`A−` / `A+`) in the header
- Omarchy Ash theme: `#121212` background, `#e0e0e0` foreground, `#626262` accent
- JetBrainsMono Nerd Font (your Omarchy font)
- Loading indicator while the EPUB is parsed
- Chapter + page progress in the header bar

## Hotkeys

- `Ctrl + T` — table of contents (arrow keys / `j` / `k` to move, Enter to open)
- `Ctrl + N` — notes panel (New button or Ctrl+Enter to add a note; Ctrl+l to edit a highlighted one)
- `Ctrl + S` — settings
- `Ctrl + h` — jump to the notes list (works even while typing a note)
- `Ctrl + l` — add a note / edit the highlighted note (loads it into the editor)
- `Ctrl + j` / `Ctrl + k` — notes list: move the highlight (wraps at the ends);
  elsewhere: cycle sections (notes list → add a note → content)
- `Ctrl + Shift + K` — keybinding reference
- `Ctrl + Shift + H` — toggle header
- `Ctrl + [` / `Ctrl + P` — home / translations browser
- `Ctrl + B` — toggle reader mode
- `Ctrl + O` — open a book file
- On the home screen: `j` / `k` select Continue where you left off /
  Translations, Enter opens
- `H` / `L` / `←` / `→` — previous / next page (always left / right)
- `J` / `K` / `↑` / `↓` (case-insensitive) — with a note highlighted: move up /
  down the notes; with content focused: step through the verses (j/↓ down, k/↑ up)
- `x` — delete the highlighted note
- `Ctrl + Shift + +/-` — grow / shrink the notes panel

## Requirements (Arch / Omarchy)

- `python` (>= 3.11)
- `python-gobject` (`gi`)
- `webkit2gtk` (WebKit2 4.1, GTK3)
- `gtk3`
- `ebooklib` (installed into the project venv)

## Install & run

```bash
# create the virtualenv (uses system PyGObject/WebKit via --system-site-packages)
python3 -m venv --system-site-packages .venv
.venv/bin/pip install ebooklib

# run
./run.sh
# or open a book directly
./run.sh /path/to/book.epub
```

Within the app, use **Open Book** in the header or `Ctrl+O`.

## Layout

```
main.py   # the entire application (parser + GTK UI + pagination engine)
run.sh    # launcher that uses the project virtualenv
```

## Why GTK3 + WebKit2 4.1?

WebKitGTK 4.1 is a GTK3 library and is already installed on Omarchy. GTK4's
WebKitGTK (6.0) is a separate package, so this app uses the GTK3 stack that
ships out of the box with zero extra system dependencies.
