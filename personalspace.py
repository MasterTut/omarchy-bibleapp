"""Pure logic for Personal Space (per-verse notes, prayer, memorization).

Kept GTK-free so the recurrence / keying rules are unit-tested. Persistence
functions live in :mod:`reader_config`; this module only shapes and reasons
about the data.
"""
from datetime import date


# ---------------- Notes (per verse) ----------------
def note_key(book_name, chapter_index, verse):
    """Key for a single per-verse personal note."""
    v = int(verse) if verse else 0
    return f"{book_name}|{chapter_index}|{v}"


# ---------------- Prayer requests ----------------
FREQS = ("daily", "weekly", "monthly")


def new_prayer(text, freq="daily"):
    if freq not in FREQS:
        freq = "daily"
    return {
        "id": _next_id([]),
        "text": text.strip(),
        "freq": freq,
        "done": False,
        "period": "",       # last period it was marked done in
    }


def _next_id(items):
    ids = [it.get("id", 0) for it in items] or [0]
    return max(ids) + 1


def add_prayer(items, text, freq="daily"):
    """Return a new list with the prayer appended (unique id assigned)."""
    items = list(items)
    p = new_prayer(text, freq)
    p["id"] = _next_id(items)
    items.append(p)
    return items


def remove_prayer(items, pid):
    return [it for it in items if it.get("id") != pid]


def period_key(freq, on):
    """The bucket a frequency resets on, for a given date."""
    y, m, d = on.year, on.month, on.day
    if freq == "daily":
        return f"{y:04d}-{m:02d}-{d:02d}"
    if freq == "weekly":
        iso = on.isocalendar()
        return f"{iso[0]:04d}-W{iso[1]:02d}"
    # monthly (and default)
    return f"{y:04d}-{m:02d}"


def toggle_prayer(items, pid, on=None):
    """Toggle a prayer's done state, stamping the current period when checked."""
    on = on or date.today()
    out = []
    for it in items:
        if it.get("id") == pid:
            it = dict(it)
            it["done"] = not it.get("done", False)
            it["period"] = period_key(it.get("freq", "daily"), on) if it["done"] else ""
        out.append(it)
    return out


def is_pending(item, on=None):
    """True if a prayer should still show as to-pray (unchecked, or a new period)."""
    on = on or date.today()
    cur = period_key(item.get("freq", "daily"), on)
    return (not item.get("done")) or (item.get("period", "") != cur)


def pending_prayers(items, on=None):
    return [it for it in items if is_pending(it, on)]


# ---------------- Memorization ----------------
def memory_key(book_name, chapter_index, verse):
    return f"{book_name}|{chapter_index}|{int(verse) if verse else 0}"


def add_memory(items, book, chapter, verse, text):
    """Add a verse to memorize (deduped by key); returns (new_list, added_bool)."""
    key = memory_key(book, chapter, verse)
    if any(m.get("key") == key for m in items):
        return items, False
    items = list(items)
    items.append({
        "key": key,
        "book": book,
        "chapter": int(chapter),
        "verse": int(verse),
        "text": text.strip(),
        "done": False,
    })
    return items, True


def remove_memory(items, key):
    return [m for m in items if m.get("key") != key]


def toggle_memory(items, key):
    out = []
    for m in items:
        if m.get("key") == key:
            m = dict(m)
            m["done"] = not m.get("done", False)
        out.append(m)
    return out


# ---------------- Bookmarks ----------------
def bookmark_key(path, chapter, page):
    """Key for a bookmark on a given chapter page of a book file."""
    return f"{path}|{int(chapter)}|{int(page)}"


def add_bookmark(items, path, src, chapter, page, label):
    """Add a bookmark for a location.

    A bookmark is unique per (path, chapter, page): adding again at the same
    location removes the existing one (a toggle). Returns ``(new_items, added)``.
    """
    key = bookmark_key(path, chapter, page)
    kept = [it for it in items if it.get("key") != key]
    if len(kept) != len(items):
        return kept, False
    kept.append({
        "id": _next_id(kept),
        "key": key,
        "path": path,
        "src": src,             # translation display name (ASV, KJV, …)
        "chapter": int(chapter),
        "page": int(page),
        "label": label,
    })
    return kept, True


def remove_bookmark(items, key):
    return [it for it in items if it.get("key") != key]


def bookmarks_sorted(items):
    """Bookmarks ordered by translation, then chapter, then page."""
    return sorted(
        items,
        key=lambda b: (str(b.get("src", "")), int(b.get("chapter") or 0), int(b.get("page") or 0)),
    )
