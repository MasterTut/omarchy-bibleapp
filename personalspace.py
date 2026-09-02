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
