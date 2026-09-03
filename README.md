# Omarchy-Bible

A minimal, beautiful EPUB e-reader/bible for Linux, styled after the **Omarchy Ash
theme**.

Built with **GTK3 + WebKitGTK (WebKit2 4.1)** and **Python 3**, rendering each
book chapter as paginated HTML in an embedded WebView, so you get faithful
typography and layout while staying true to the Omarchy look.

## Features

- Bible translations browser with an **Import EPUB** option on the home screen
- Import validates that the EPUB can be parsed into books/chapters/verses
- Continue reading (last viewed book and page)
- Page-specific notes (saved to JSON, bottom panel); a highlighted note can be
  loaded into the editor with `Ctrl + l` and saved in place; a New button always
  lets you start a fresh note
- Notes show the referenced verse or `General notes` next to the timestamp
- Settings panel (`Ctrl + S`) with auto-hide top bar toggle (persisted to `settings.json`)
- Toggleable header (`Ctrl + Shift + H`)
- Hierarchical table of contents (`Ctrl + T`): books → chapters → verses, navigable with arrow keys and Neo-Vim `j`/`k`
- Paginated reading (page breaks fit the viewport, no scrolling the text)
- Verse-by-verse navigation in the reader (`j` / `k` highlight each verse and
  scroll it into view)
- Reference / commentary panel (`Ctrl + R`): study-Bible footnote and
  cross-reference markers (`[1]`, `<sup>`, `title="…"` links) are extracted per
  verse and shown in a hideable strip above the notes; clicking a marker in the
  text pins its note
- Tabbed Resources panel (`Ctrl + R`): **Notes · Cross-refs · Introduction ·
  Images · Links**. Switch tabs with the mouse or the number keys `1`–`5`. For
  study Bibles (e.g. Crossway ESV) it surfaces the per-book introduction,
  images/maps/charts (click to open in your viewer), and external web links
  (click to open in your browser)
- **Offline original-language word study** (`i` in the reader): enter word mode
  and step through the Hebrew (OT) or Greek (NT) words of the current verse with
  `h`/`l`; the **Word** tab of the Resources panel shows each word's original
  script, transliteration, parsing, Strong's number and definition. The
  interlinear + Strong's data ships offline under `data/lexicon/`
- Paragraphs are automatically split so each verse appears on its own line
  (Crossway-style EPUBs with many verses per paragraph are reformatted on load)
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

- `Ctrl + T` — table of contents: books → chapters → verses (arrow keys / `j` /
  `k` to move, `Enter` to open, `h` / `Back` / `Esc` to go back)
- `Ctrl + P` — Personal Space panel (Notes · Prayer · Memory). Two modes:
  navigate (default — `Tab`/`1-3`/`h`/`l` switch tabs, `i` enters the field)
  and edit (typing; `Esc` back to navigate; `Ctrl+Enter` saves a note)
- `Ctrl + R` — Resources panel above the notes: **1 Notes · 2 Cross-refs ·
  3 Intro · 4 Images · 5 Links · 6 Word**. When focused, `j`/`k` scroll it and
  `h`/`l` switch tabs (this does not move your place in the reading content);
  `Ctrl + Shift + +/-` resizes it
- `Ctrl + S` — settings
- `Ctrl + h` / `Ctrl + l` — focus the Personal Space notes editor
- `Ctrl + j` / `Ctrl + k` — move focus between content ⇄ personal space ⇄
  resources (also works while typing; only cycles panels that are open)
- Mouse: click the reading area, the Personal Space panel, or the resources
  panel to move focus there directly
- `Ctrl + Shift + K` — keybinding reference
- `Ctrl + Shift + H` — toggle header
- `Ctrl + [` — home / translations browser
- `/` — search a book or passage (e.g. `John 3:16`, `gen 50`, `ps 23`); `↑/↓`
  select, `Enter` jumps to the chapter (and highlights the verse), `Esc` closes
- `Ctrl + B` — toggle reader mode
- `Ctrl + O` — open a book file (disabled on the home screen)
- `Ctrl + I` — import an EPUB into the library (on Home: `i`)
- On the home screen: `j` / `k` select Continue where you left off /
  Translations / Import EPUB, Enter opens
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
main.py             # app shell: window, dock, mode line, key handling, navigation
ui_toc.py           # books → chapters → verses table-of-contents overlay (mixins)
ui_resources.py     # tabbed Resources panel (notes / cross-refs / word study)
ui_settings.py      # settings + keybinding-reference overlays
ui_search.py        # "go to passage" search overlay
lexicon.py          # offline interlinear + Strong's lookup (GTK-free)
document.py         # reading state (single source of truth)
epubsource.py       # EPUB parsing into books/chapters/verses (+ assets)
epubtext.py         # chapter → paginated HTML rendering
personalspace.py    # notes / prayer / memory persistence
verse_ref.py        # passage-reference parsing
reader_config.py    # settings + theme helpers
reader_assets.py    # stylesheet + page JS
scripts/prepare_lexicon.py  # regenerates data/lexicon/*.json from data/raw/*
run.sh              # launcher that uses the project virtualenv
```

## Data sources & licences

The offline interlinear and Strong's datasets under `data/lexicon/` are derived
from the following open-source resources and are rebuilt with
`scripts/prepare_lexicon.py` (sources cloned under `data/raw/`):

| Component | Source | Licence |
| --- | --- | --- |
| NT Greek text + morphology | [MorphGNT SBLGNT](https://github.com/morphgnt/sblgnt) | SBLGNT text: [SBLGNT EULA](https://sblgnt.com/license/); morphology: CC-BY-SA 3.0 |
| OT Hebrew text + morphology | [OpenScriptures Hebrew Bible (morphhb)](https://github.com/openscriptures/morphhb) | WLC text: Public Domain; morphology: CC-BY 4.0 |
| Greek lemma → Strong's | [jtauber/greek-lemma-mappings](https://github.com/jtauber/greek-lemma-mappings) | CC-BY-SA 4.0 |
| Strong's dictionaries (G/H) | [openscriptures/strongs](https://github.com/openscriptures/strongs) — Strong, *Exhaustive Concordance* (1890/1894) | Public Domain (CC-BY-SA on the JSON derivative) |

Attribution:
- MorphGNT SBLGNT — James K. Tauber and contributors
- OpenScriptures Hebrew Bible Project
- `greek-lemma-mappings` — James Tauber (CC-BY-SA 4.0)
- Strong's *Exhaustive Concordance of the Bible* — James Strong (1890/1894);
  JSON conversion by the Open Scriptures project (Michael Boler, David
  Instone-Brewer, Ulrik Petersen)

## Why GTK3 + WebKit2 4.1?

WebKitGTK 4.1 is a GTK3 library and is already installed on Omarchy. GTK4's
WebKitGTK (6.0) is a separate package, so this app uses the GTK3 stack that
ships out of the box with zero extra system dependencies.
