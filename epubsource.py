"""EpubSource: loads an EPUB and answers "what does this book contain?".

This module is deliberately free of any GTK / WebView dependency so the fragile
book-format parsing (KJV / ASV / Crossway / ESV Study, etc.) can be unit tested
headlessly and reused by the UI, a CLI, or future features.

Responsibilities
----------------
* Read the EPUB (ebooklib) and extract its items to a temp dir.
* Build a flat list of chapters plus a book -> chapter tree (``books``).
* Render a single chapter to annotated HTML (``chapter_html``).
* Provide, lazily and cached, the per-chapter verse list, the per-verse study
  notes / cross references, and the book-level resources (introduction text,
  images/maps/charts, external web links).
"""
import collections
import html
import os
import re
import shutil
import tempfile

import ebooklib
from ebooklib import epub

import epubtext


class EpubSource:
    """Parse one EPUB into chapters and per-chapter/per-book data."""

    def __init__(self, path):
        self.path = path
        self.book = None
        self.bookdir = None
        self.item_paths = {}
        self.chapters = []          # list of (book_name, title, path, item_name)
        self.books = []             # [{"title", "chapters": [{"title","index"}]}]
        self._slice = {}            # chapter_index -> (start, end) offsets into file
        self._verses = {}           # chapter_index -> [int]
        self._refs = {}             # chapter_index -> {verse: [entry]}
        self._res = {}              # chapter_index -> (intro, images, links)
        self._text_cache = {}       # extracted file path -> decoded text

    # ---------------- Loading ----------------
    def load(self):
        """Read the EPUB and build the chapter model. Returns True if usable."""
        self.book = epub.read_epub(self.path)
        if self.bookdir:
            shutil.rmtree(self.bookdir, ignore_errors=True)
        self.bookdir = tempfile.mkdtemp(prefix="omabible-")

        self.item_paths = {}
        for item in self.book.get_items():
            name = item.get_name()
            if not name:
                continue
            safe = os.path.join(self.bookdir, name)
            os.makedirs(os.path.dirname(safe) or self.bookdir, exist_ok=True)
            try:
                with open(safe, "wb") as f:
                    f.write(item.get_content())
                self.item_paths[name] = safe
            except Exception:
                pass

        if self._looks_like_study_bible() and self._prepare_study_bible():
            return bool(self.chapters)
        self._prepare_from_toc()
        return bool(self.chapters)

    def close(self):
        if self.bookdir:
            shutil.rmtree(self.bookdir, ignore_errors=True)
            self.bookdir = None

    # ---------------- Public queries ----------------
    def chapter_html(self, index):
        """Render one chapter to display-ready HTML (annotated + one verse/line)."""
        return epubtext.split_verses_into_lines(self.chapter_body(index))

    def chapter_body(self, index):
        """Return the annotated (data-vn tagged) body HTML for one chapter."""
        if not 0 <= index < len(self.chapters):
            return ""
        _, _, path, _ = self.chapters[index]
        try:
            with open(path, "rb") as f:
                content = f.read().decode("utf-8", "replace")
        except Exception:
            return ""
        sl = self._slice.get(index)
        if sl:
            content = content[sl[0]:sl[1]]
        body = epubtext.annotate_verses(epubtext.extract_body(content))
        return epubtext.inject_first_verse(body)

    def verses(self, index):
        """Sorted list of verse numbers present in a chapter (cached)."""
        if index not in self._verses:
            body = self.chapter_body(index)
            self._verses[index] = sorted(
                {int(n) for n in re.findall(r'data-vn="(\d+)"', body)}
            )
        return self._verses[index]

    def refs(self, index):
        """{verse_number: [{label, text, kind}]} study notes / cross-refs."""
        if index not in self._refs:
            if not 0 <= index < len(self.chapters):
                self._refs[index] = {}
            else:
                _, _, path, _ = self.chapters[index]
                self._refs[index] = self._extract_refs(
                    self.chapter_body(index), os.path.dirname(path)
                )
        return self._refs[index]

    def resources(self, index):
        """Return (intro_text, images, links) for the book owning this chapter."""
        if index in self._res:
            return self._res[index]
        empty = ("", [], [])
        if not 0 <= index < len(self.chapters):
            return empty
        result = self._compute_resources(index)
        self._res[index] = result
        return result

    def chapter_path(self, index):
        if 0 <= index < len(self.chapters):
            return self.chapters[index][2]
        return None

    # ---------------- TOC / normal EPUBs ----------------
    def _prepare_from_toc(self):
        entries = self._flatten_toc()
        if not entries:
            entries = self._parse_ncx()

        expanded = []
        for title, href in entries:
            intro_name = self._resolve_href(href)
            intro_path = self.item_paths.get(intro_name)
            if intro_path:
                links = self._extract_chapter_links(intro_path)
                if links:
                    for link_text, ch_href in links:
                        ch_name = self._resolve_href(ch_href)
                        ch_path = self.item_paths.get(ch_name)
                        if ch_path:
                            expanded.append((title, f"{title} {link_text}", ch_path, ch_name))
                    continue
            if intro_path:
                expanded.append((title, title or "Chapter", intro_path, intro_name))

        if not expanded:
            for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                name = item.get_name()
                path = self.item_paths.get(name)
                if not path:
                    continue
                title = getattr(item, "title", None) or os.path.basename(name)
                expanded.append((title, title, path, name))

        books_map = {}
        for book_title, ch_title, path, name in expanded:
            books_map.setdefault(book_title, []).append((ch_title, path, name))

        if all(len(chs) == 1 for chs in books_map.values()):
            books_map = {}
            for book_title, ch_title, path, name in expanded:
                m = re.match(r"^(.*?)\s+(\d+)$", (ch_title or "").strip())
                bt = m.group(1).strip() if m else book_title
                books_map.setdefault(bt, []).append((ch_title, path, name))

        self.chapters = []
        self.books = []
        for book_title, chs in books_map.items():
            book_chapters = []
            for ch_title, path, name in chs:
                idx = len(self.chapters)
                self.chapters.append((book_title, ch_title, path, name))
                book_chapters.append({"title": ch_title, "index": idx})
            self.books.append({"title": book_title, "chapters": book_chapters})

    def _extract_chapter_links(self, intro_path):
        """Chapter file links inside a book intro page (Crossway book-only TOCs)."""
        try:
            with open(intro_path, "rb") as f:
                content = f.read().decode("utf-8", "replace")
        except Exception:
            return []
        body = epubtext.extract_body(content)
        links = re.findall(
            r'<a\s+[^>]*href="([^"]*)"[^>]*>(.*?)</a>', body, flags=re.I | re.S
        )
        result = []
        for href, text in links:
            text = re.sub(r"<[^>]+>", "", text)
            text = text.replace("&nbsp;", " ").replace("\xa0", " ").strip()
            if not text or not re.search(r"\bchapter\b|\bch\b", text, flags=re.I):
                continue
            ch_name = self._resolve_href(href)
            if not ch_name or ch_name not in self.item_paths:
                continue
            result.append((text, href))
        return result

    def _flatten_toc(self):
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
        if not href:
            return ""
        target = href.split("#")[0].replace("\\", "/")
        while target.startswith("./"):
            target = target[2:]
        if target in self.item_paths:
            return target
        stripped = target.lstrip("./")
        for name in self.item_paths:
            if name.lstrip("./") == stripped:
                return name
        return ""

    def _parse_ncx(self):
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

    # ---------------- Crossway ESV Study Bible ----------------
    def _looks_like_study_bible(self):
        for name in self.item_paths:
            if re.search(r'(?:^|/)b\d{2}\.\d{2}\..+\.text\.html$', name):
                return True
        return False

    def _prepare_study_bible(self):
        by_book = collections.OrderedDict()
        for name, path in self.item_paths.items():
            m = re.search(r'(?:^|/)b(\d{2})\.(\d{2})\.(.+?)\.text\.html$', name)
            if m:
                by_book.setdefault(m.group(1), []).append(
                    (int(m.group(2)), name, path, m.group(3))
                )
        if not by_book:
            return False

        self.chapters = []
        self.books = []
        self._slice = {}
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
                    self._slice[idx] = (start, end)
                    book_chapters.append({"title": title, "index": idx})
            if book_chapters:
                self.books.append(
                    {"title": book_name or f"Book {int(bid)}", "chapters": book_chapters}
                )
        return bool(self.chapters)

    # ---------------- References (study notes / cross refs) ----------------
    def _extract_refs(self, body, chapter_dir):
        anchor_re = re.compile(r'data-vn="(\d+)"|<a\b([^>]*?)>(.*?)</a>', re.I | re.S)
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
            if re.match(r"c\d+\.\w", want_id):
                seg = src[body_from:body_from + 1200]
                stop = re.search(r'<span class="crossref-|</p>|<h\d', seg)
                if stop:
                    seg = seg[:stop.start()]
                return clean(seg)
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
            block_end = close if close != -1 else body_from + 2500
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
            src = self._text_cache.get(fname)
            if src is None:
                try:
                    with open(fname, "rb") as f:
                        src = f.read().decode("utf-8", "replace")
                except Exception:
                    src = ""
                self._text_cache[fname] = src
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
            low = (href or "").lower()
            frag = href.rsplit("#", 1)[-1] if "#" in href else ""
            kind = "crossrefs" if ("crossref" in low or frag[:1] == "c") else "notes"
            entry = {"label": label, "text": text, "kind": kind}
            lst = refs.setdefault(current, [])
            if not any(x["label"] == label and x["text"] == text for x in lst):
                lst.append(entry)
        return refs

    # ---------------- Book-level resources ----------------
    def _compute_resources(self, index):
        book_name, _, path, name = self.chapters[index]
        intro_text, images, links = "", [], []
        m = re.search(r'(b\d{2})\.(\d{2})\.', name)
        intro_body = ""
        intro_dir = ""
        if not m:
            return ("", [], [])
        bid = m.group(1)
        intro_name = None
        for candidate in self.item_paths:
            if re.search(rf'(?:^|/){bid}\.\d{{2}}\..+\.intros\.html$', candidate):
                intro_name = candidate
                break
        if intro_name:
            try:
                with open(self.item_paths[intro_name], "rb") as f:
                    raw = f.read().decode("utf-8", "replace")
                intro_body = epubtext.extract_body(raw)
                intro_text = epubtext.html_to_text(intro_body)
                intro_dir = os.path.dirname(self.item_paths[intro_name])
            except Exception:
                intro_body = ""

        seen = set()
        for im in re.finditer(r'<img[^>]*\bsrc="([^"]+)"', intro_body, flags=re.I):
            src = im.group(1)
            base = os.path.basename(src)
            cand = os.path.normpath(os.path.join(intro_dir, src)) if intro_dir else ""
            if not (cand and os.path.isfile(cand)):
                cand = next(
                    (p for n, p in self.item_paths.items() if os.path.basename(n) == base),
                    "",
                )
            if cand and cand not in seen:
                seen.add(cand)
                images.append({"path": cand, "caption": base})

        link_seen = set()
        scan_names = [
            n for n in self.item_paths
            if re.search(rf'(?:^|/){bid}\.\d{{2}}\..+\.(?:intros|studynotes|crossrefs)\.html$', n)
        ]
        for n in scan_names:
            try:
                with open(self.item_paths[n], "rb") as f:
                    txt = f.read().decode("utf-8", "replace")
            except Exception:
                continue
            for lm in re.finditer(
                r'<a[^>]*\bhref="(https?://[^"]+)"[^>]*>(.*?)</a>', txt, flags=re.I | re.S
            ):
                url = lm.group(1)
                if url in link_seen:
                    continue
                link_seen.add(url)
                label = re.sub(r"<[^>]+>", "", lm.group(2)).strip()
                links.append({"url": url, "text": label or url})
        return (intro_text, images, links)
