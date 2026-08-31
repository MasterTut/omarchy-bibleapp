# Omarchy Reader

A minimal, beautiful EPUB e-reader for Linux, styled after the **Omarchy Ash
theme**.

Built with **GTK3 + WebKitGTK (WebKit2 4.1)** and **Python 3**, rendering each
book chapter as paginated HTML in an embedded WebView, so you get faithful
typography and layout while staying true to the Omarchy look.

## Features

- Open `.epub` books via file dialog or command line
- Paginated reading (page breaks fit the viewport, no scrolling the text)
- Keyboard navigation: `←`/`→`, `PageUp`/`PageDown`, `Space`
- Auto-advance across chapters (including skipping empty pages)
- Font size controls (`A−` / `A+`) in the header
- Omarchy Ash theme: `#121212` background, `#e0e0e0` foreground, `#626262` accent
- JetBrainsMono Nerd Font (your Omarchy font)
- Loading indicator while the EPUB is parsed
- Chapter + page progress in the header bar

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
