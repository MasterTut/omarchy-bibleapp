"""Pure verse/book reference search over a parsed book tree (GTK-free, testable).

``books`` is the same structure EpubSource builds: a list of
``{"title": str, "chapters": [{"title": str, "index": int}, ...]}``.

``search()`` turns a query like "gen", "John 3:16", "1 cor 13",
"psalm 23 1" or "Genesis 1:20-30" into a ranked list of jump targets. Each
result puts the available scope in the row so the UI can auto-complete:
book rows carry ``chapters_total`` and chapter/verse/range rows carry
``verses_total`` (when a ``verse_count`` callback is supplied).
"""
import re

# Common single-token abbreviations -> canonical word (matched against book
# titles). Deliberately small; prefix matching handles most real book names.
_ABBR = {
    "gen": "genesis", "exo": "exodus", "exod": "exodus", "lev": "leviticus",
    "num": "numbers", "nb": "numbers", "deu": "deuteronomy", "deut": "deuteronomy",
    "josh": "joshua", "jdg": "judges", "1sa": "1 samuel", "2sa": "2 samuel",
    "1ki": "1 kings", "2ki": "2 kings", "1ch": "1 chronicles", "2ch": "2 chronicles",
    "ezr": "ezra", "neh": "nehemiah", "est": "esther", "job": "job",
    "ps": "psalms", "psa": "psalms", "psalm": "psalms", "prov": "proverbs",
    "eccl": "ecclesiastes", "eccles": "ecclesiastes", "sos": "song of solomon",
    "isa": "isaiah", "jer": "jeremiah", "lam": "lamentations", "ezk": "ezekiel",
    "dan": "daniel", "hos": "hosea", "jonah": "jonah", "jon": "jonah",
    "mic": "micah", "zep": "zephaniah", "hag": "haggai", "zec": "zechariah",
    "mal": "malachi", "mat": "matthew", "mt": "matthew", "matt": "matthew",
    "mrk": "mark", "mk": "mark", "mar": "mark", "luk": "luke", "jn": "john",
    "joh": "john", "act": "acts", "rom": "romans", "1co": "1 corinthians",
    "2co": "2 corinthians", "gal": "galatians", "eph": "ephesians",
    "php": "philippians", "col": "colossians", "1th": "1 thessalonians",
    "2th": "2 thessalonians", "1ti": "1 timothy", "2ti": "2 timothy",
    "tit": "titus", "phm": "philemon", "heb": "hebrews", "jas": "james",
    "1pe": "1 peter", "2pe": "2 peter", "1jo": "1 john", "2jo": "2 john",
    "3jo": "3 john", "jud": "jude", "rev": "revelation",
}


def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _expand(query_norm):
    return " ".join(_ABBR.get(t, t) for t in query_norm.split())


def _chapter_numbers(chapters):
    """Map a chapter's number (from its trailing digits, else 1-based order)
    to its global chapter index."""
    out = {}
    for i, c in enumerate(chapters):
        m = re.search(r"(\d+)\s*$", str(c.get("title", "")) or "")
        num = int(m.group(1)) if m else (i + 1)
        out.setdefault(num, c["index"])
    return out


def _find_books(books, book_query):
    nb = _norm(book_query)
    if not nb:
        return []
    nbe = _expand(nb)
    scored = []
    for i, b in enumerate(books):
        bt = _norm(b["title"])
        score = None
        if bt == nb or bt == nbe:
            score = 0
        elif bt.startswith(nb) or bt.startswith(nbe):
            score = 1
        elif nb in bt or (nbe and nbe in bt):
            score = 2
        if score is not None:
            scored.append((score, i, b))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [b for _, _, b in scored]


def search(books, query, limit=20, verse_count=None):
    """Return ranked jump targets: {label, chapter_index, verse, kind, ...}.

    Kinds are ``book`` (whole book), ``chapter`` (whole chapter),
    ``verse`` (single verse) and ``range`` (``Genesis 1:20-30``). Chapter,
    verse and range rows gain ``verse_end``/``verses_total`` when a
    ``verse_count`` callback (global chapter index -> verse count) is given.
    """
    q = (query or "").strip()
    if not q or not books:
        return []
    results, seen = [], set()

    m = re.match(r"^(.*?)\s+(\d+)(?:[\s:.]+(\d+)(?:-(\d+))?)?$", q)
    book_part = m.group(1).strip() if m else q
    chapter = int(m.group(2)) if m else None
    verse = int(m.group(3)) if (m and m.group(3)) else None
    verse_end = int(m.group(4)) if (m and m.group(4)) else None

    if verse_end is not None and verse is not None and verse_end < verse:
        verse, verse_end = verse_end, verse

    # Exact "book chapter[:verse[-end]]" first.
    if chapter is not None:
        for b in _find_books(books, book_part):
            nums = _chapter_numbers(b["chapters"])
            if chapter in nums:
                idx = nums[chapter]
                kind = "range" if (verse is not None and verse_end is not None) else (
                    "verse" if verse is not None else "chapter"
                )
                label = f"{b['title']} {chapter}"
                if verse is not None:
                    label += f":{verse}"
                    if verse_end is not None:
                        label += f"-{verse_end}"
                key = ("c", idx, verse, verse_end)
                if key in seen:
                    continue
                seen.add(key)
                row = {
                    "label": label, "chapter_index": idx,
                    "verse": verse or 0, "kind": kind,
                }
                if verse_end is not None:
                    row["verse_end"] = verse_end
                if verse_count is not None:
                    total = verse_count(idx)
                    if total:
                        row["verses_total"] = total
                results.append(row)
                if len(results) >= limit:
                    return results

    # Then whole-book jumps (first chapter) + matching book names.
    for b in _find_books(books, book_part):
        if not b["chapters"]:
            continue
        idx = b["chapters"][0]["index"]
        key = ("b", idx)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "label": b["title"], "chapter_index": idx,
            "verse": 0, "kind": "book",
            "chapters_total": len(b["chapters"]),
        })
        if len(results) >= limit:
            break
    return results
