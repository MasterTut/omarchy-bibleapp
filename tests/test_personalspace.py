"""Headless tests for Personal Space logic (notes keys, prayer reset, memory).

    .venv/bin/python tests/test_personalspace.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import personalspace as ps  # noqa: E402


def run():
    # --- note key ---
    assert ps.note_key("KJV.epub", 0, 13) == "KJV.epub|0|13"
    assert ps.note_key("KJV.epub", 0, 0) == "KJV.epub|0|0"

    # --- prayer: unique ids, add/remove ---
    items = []
    items = ps.add_prayer(items, "Healing", "daily")
    items = ps.add_prayer(items, "Nations", "weekly")
    assert [p["text"] for p in items] == ["Healing", "Nations"]
    assert items[0]["id"] != items[1]["id"]
    items = ps.add_prayer(items, "x")  # default daily
    assert items[2]["freq"] == "daily"

    # --- prayer: auto-reset checklist ---
    p = ps.add_prayer([], "Pray", "daily")[0]
    assert ps.is_pending(p, date(2026, 1, 1))
    done = ps.toggle_prayer([p], p["id"], on=date(2026, 1, 1))[0]
    assert done["done"] and not ps.is_pending(done, date(2026, 1, 1))
    # next day -> pending again
    assert ps.is_pending(done, date(2026, 1, 2))
    # unchecking makes it pending immediately
    undone = ps.toggle_prayer([done], p["id"], on=date(2026, 1, 1))[0]
    assert not undone["done"] and ps.is_pending(undone, date(2026, 1, 1))

    # weekly resets across ISO week boundary but not mid-week
    w = ps.add_prayer([], "W", "weekly")[0]
    w = ps.toggle_prayer([w], w["id"], on=date(2026, 1, 5))[0]   # Mon
    assert not ps.is_pending(w, date(2026, 1, 7))               # Wed same week
    assert ps.is_pending(w, date(2026, 1, 12))                  # next Mon
    # monthly resets across months, same month stays done
    m = ps.add_prayer([], "M", "monthly")[0]
    m = ps.toggle_prayer([m], m["id"], on=date(2026, 3, 1))[0]
    assert not ps.is_pending(m, date(2026, 3, 30))
    assert ps.is_pending(m, date(2026, 4, 1))

    # --- pending filter ---
    grp = ps.add_prayer([], "A", "daily")
    grp = ps.add_prayer(grp, "B", "daily")
    grp = ps.toggle_prayer(grp, grp[0]["id"], on=date(2026, 5, 1))
    assert ps.pending_prayers(grp, on=date(2026, 5, 1)) == [grp[1]]

    # --- memory: add dedupe, remove, toggle ---
    mem = []
    mem, added = ps.add_memory(mem, "KJV.epub", 0, 1, "In the beginning...")
    assert added and len(mem) == 1
    mem, added = ps.add_memory(mem, "KJV.epub", 0, 1, "dup")
    assert not added and len(mem) == 1
    mem, added = ps.add_memory(mem, "KJV.epub", 0, 2, "And the earth...")
    assert added and len(mem) == 2
    k = mem[0]["key"]
    mem = ps.toggle_memory(mem, k)
    assert mem[0]["done"]
    mem = ps.remove_memory(mem, k)
    assert len(mem) == 1 and all(mm["key"] != k for mm in mem)

    # --- bookmarks: add, toggle, remove, sort ---
    bm = []
    bm, added = ps.add_bookmark(bm, "/x/KJV.epub", "KJV", 3, 2, "John 3 · p.3")
    assert added and len(bm) == 1
    assert bm[0]["key"] == ps.bookmark_key("/x/KJV.epub", 3, 2)
    # adding the same location twice removes it (toggle)
    bm, added = ps.add_bookmark(bm, "/x/KJV.epub", "KJV", 3, 2, "dup")
    assert not added and bm == []
    bm, added = ps.add_bookmark(bm, "/x/KJV.epub", "KJV", 3, 2, "A")
    bm, added = ps.add_bookmark(bm, "/x/ASV.epub", "ASV", 0, 0, "B")
    bm, added = ps.add_bookmark(bm, "/x/KJV.epub", "KJV", 0, 5, "C")
    order = [b["label"] for b in ps.bookmarks_sorted(bm)]
    assert order == ["B", "C", "A"], order
    bm = ps.remove_bookmark(bm, "/x/KJV.epub|3|2")
    assert all(b["key"] != "/x/KJV.epub|3|2" for b in bm)
    assert len(bm) == 2

    print("ALL PERSONAL SPACE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run())
