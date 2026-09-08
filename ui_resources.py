"""Resources panel (Study Notes / Cross-Refs / Intro / Images / Links / Word) mixin."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
import os
import re
import lexicon


class ResourcesMixin:
    def _build_refs_overlay(self):
        """Build the tabbed Resources panel (a strip above the notes)."""
        # EventBox gives the panel a real GdkWindow so a click anywhere in it
        # focuses it (its label children are windowless and would not).
        self._refs_overlay = Gtk.EventBox()
        self._refs_overlay.set_visible(False)
        self._refs_overlay.set_halign(Gtk.Align.FILL)
        self._refs_overlay.set_valign(Gtk.Align.END)
        self._refs_overlay.set_size_request(-1, self._refs_panel_height)
        self._refs_overlay.get_style_context().add_class("refs-overlay")
        self._refs_overlay.connect(
            "button-press-event", lambda *_: self._focus_refs()
        )
        self._refs_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._refs_overlay.add(self._refs_inner)
        outer = self._refs_inner

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        bar.set_margin_top(10)
        bar.set_margin_bottom(6)

        title = Gtk.Label(label="Resources")
        title.get_style_context().add_class("title-label")
        title.set_halign(Gtk.Align.START)
        bar.pack_start(title, False, False, 0)

        self.refs_loc = Gtk.Label(label="")
        self.refs_loc.get_style_context().add_class("progress-label")
        bar.pack_end(self.refs_loc, False, False, 0)

        close = Gtk.Button(label="\u2715")
        close.connect("clicked", lambda *_: self._hide_refs())
        bar.pack_end(close, False, False, 0)

        outer.pack_start(bar, False, False, 0)

        # Tab bar: Notes · Cross-refs · Introduction · Images · Links.
        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tabs.set_margin_start(16)
        tabs.set_margin_end(16)
        tabs.set_margin_bottom(8)
        self._ref_tab_buttons = {}
        self._ref_tab_names = {}
        for key, name in (
            ("notes", "Study Notes"),
            ("crossrefs", "Cross-Refs"),
            ("intro", "Intro"),
            ("images", "Images"),
            ("links", "Links"),
            ("word", "Word"),
        ):
            self._ref_tab_names[key] = name
            btn = Gtk.Button(label=self._tab_label(name, False))
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("tab-btn")
            btn.connect("clicked", lambda _b, k=key: self._set_ref_tab(k))
            tabs.pack_start(btn, False, False, 0)
            self._ref_tab_buttons[key] = btn
        outer.pack_start(tabs, False, False, 0)

        self._refs_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._refs_body.set_margin_start(16)
        self._refs_body.set_margin_end(16)
        self._refs_body.set_margin_bottom(12)
        self._refs_scroller = scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_can_focus(True)
        scroller.connect("button-press-event", lambda *_: self._focus_refs())
        scroller.add(self._refs_body)
        scroller.get_style_context().add_class("refs-scroller")

        outer.pack_start(scroller, True, True, 0)
    def _set_ref_tab(self, key):
        if key not in self._ref_tab_buttons:
            return
        if key != "notes":
            self._refs_pinned = None
        self._refs_tab = key
        self._refresh_refs()
        self._focus_refs()
    def _next_ref_tab(self, delta):
        order = [k for k in self._ref_tab_buttons if self._ref_tab_buttons[k].get_visible()]
        if not order:
            return None
        if self._refs_tab not in order:
            return order[0]
        idx = (order.index(self._refs_tab) + delta) % len(order)
        return order[idx]
    def _sync_ref_tab_buttons(self):
        for key, btn in self._ref_tab_buttons.items():
            active = key == self._refs_tab
            ctx = btn.get_style_context()
            if active:
                ctx.add_class("tab-active")
            else:
                ctx.remove_class("tab-active")
            if key == "word" and self._refs_overlay and self._refs_overlay.get_visible():
                name = self._get_word_tab_label()
            else:
                name = self._ref_tab_names.get(key, key)
            btn.set_label(self._tab_label(name, active))
    def _tab_label(self, name, active):
        return ("[\u25b8 " if active else "[ ") + name + " ]"
    def _focus_in_refs(self):
        """True when keyboard focus is inside the Resources panel."""
        if self._refs_overlay is None:
            return False
        widget = self.window.get_focus()
        while widget is not None:
            if widget is self._refs_overlay:
                return True
            if widget is self._notes_overlay or widget is self.toc_overlay:
                return False
            widget = widget.get_parent()
        return False
    def _scroll_refs(self, delta, big=False):
        if self._refs_scroller is None:
            return
        adj = self._refs_scroller.get_vadjustment()
        step = adj.get_page_increment() if big else adj.get_step_increment()
        if step <= 0:
            step = 240 if big else 30
        new = adj.get_value() + delta * step
        new = max(adj.get_lower(), min(new, adj.get_upper() - adj.get_page_size()))
        adj.set_value(new)
    def _show_refs(self):
        if not self.book_path or not self.chapters:
            return
        self._refs_overlay.set_visible(True)
        self._position_refs_above_notes()
        self._refresh_refs()
        self._focus_refs()
    def _hide_refs(self):
        if self._refs_overlay is not None:
            self._refs_overlay.set_visible(False)
        if self._focus == "refs":
            self._focus = "content"
            self._active_section = "content"
        if self.webview and not self._on_home:
            self.webview.grab_focus()
        self._update_header_focus()
    def _toggle_refs(self):
        if self._refs_overlay is not None and self._refs_overlay.get_visible():
            self._hide_refs()
        else:
            self._show_refs()
    def _ref_placeholder(self, text):
        lbl = Gtk.Label(label=text)
        lbl.get_style_context().add_class("progress-label")
        lbl.set_halign(Gtk.Align.START)
        lbl.set_xalign(0.0)
        lbl.set_line_wrap(True)
        self._refs_body.pack_start(lbl, False, False, 0)
    def _ref_card(self, label, text):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.get_style_context().add_class("ref-card")
        if label:
            head = Gtk.Label(label=f"[{label}]")
            head.get_style_context().add_class("toc-current")
            head.set_halign(Gtk.Align.START)
            card.pack_start(head, False, False, 0)
        body = Gtk.Label(label=text)
        body.set_halign(Gtk.Align.START)
        body.set_xalign(0.0)
        body.set_line_wrap(True)
        body.set_selectable(True)
        card.pack_start(body, False, False, 0)
        self._refs_body.pack_start(card, False, False, 0)
    def _inspect_ref(self):
        """(book_index_1to66, chapter, verse) for the current word-study target."""
        if not self.doc or not (0 <= self.chapter_index < len(self.chapters)):
            return None, 0, 0
        book_display = self.chapters[self.chapter_index][0] or ""
        bnum = lexicon.book_number(book_display)
        m = re.search(r"(\d+)\s*$", self.chapters[self.chapter_index][1] or "")
        cnum = int(m.group(1)) if m else (self.chapter_index + 1)
        verse = self._current_verse or 1
        return bnum, cnum, verse
    def _word_row(self, w, index=0, active=False):
        strongs_code = w.get("strongs", "")
        entry = lexicon.strongs(strongs_code) or {}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.get_style_context().add_class("ref-card")
        if active:
            box.get_style_context().add_class("word-active")
        head = Gtk.Label()
        head.set_markup(
            "{}<b>{}</b>  <span foreground='{}'>{} · {} {}</span>".format(
                "\u25b8 " if active else "  ",
                GLib.markup_escape_text(w.get("w", "")),
                "#888888",
                GLib.markup_escape_text(w.get("translit", "") or w.get("lemma", "")),
                GLib.markup_escape_text(w.get("pos", "")),
                GLib.markup_escape_text(strongs_code),
            )
        )
        head.set_xalign(0.0)
        box.pack_start(head, False, False, 0)
        detail = entry.get("def") if active else (entry.get("gloss") or "")
        detail = detail or entry.get("gloss") or ""
        if detail:
            g = Gtk.Label(label=detail)
            g.set_xalign(0.0)
            g.set_line_wrap(True)
            g.get_style_context().add_class("progress-label")
            box.pack_start(g, False, False, 0)
        self._refs_body.pack_start(box, False, False, 0)
    def _open_uri(self, uri):
        """Open a file:// or http(s) URI with the system default handler."""
        try:
            if hasattr(Gtk, "show_uri_on_window"):
                Gtk.show_uri_on_window(self.window, uri, Gdk.CURRENT_TIME)
            else:
                Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception:
            GLib.spawn_command_line_async(f"xdg-open {shlex.quote(uri)}")
    def _get_tab_data_counts(self):
        """Return {tab_key: count} for the current chapter/verse."""
        verse = getattr(self, "_current_verse", 0)
        book_name = self.chapters[self.chapter_index][0] if 0 <= self.chapter_index < len(self.chapters) else ""
        counts = {}
        if self.source:
            if verse:
                allrefs = self.source.refs(self.chapter_index)
                counts["notes"] = len([e for e in allrefs.get(verse, []) if e.get("kind", "notes") == "notes"])
                counts["crossrefs"] = len([e for e in allrefs.get(verse, []) if e.get("kind", "notes") == "crossrefs"])
            intro, images, links = self.source.resources(self.chapter_index)
            counts["intro"] = 1 if intro.strip() else 0
            counts["images"] = len(images)
            counts["links"] = len(links)
        bnum, cnum, v = self._inspect_ref()
        words = lexicon.interlinear(bnum, cnum, v) if bnum else []
        counts["word"] = len(words)
        return counts

    def _get_word_tab_label(self):
        bnum, _, _ = self._inspect_ref()
        if bnum and lexicon.is_ot(bnum):
            return "Hebrew"
        return "Greek"

    def _scroll_to_active_word(self):
        if not self._refs_overlay or not self._refs_overlay.get_visible():
            return
        if self._refs_tab != "word":
            return
        for child in self._refs_body.get_children():
            if child.get_style_context().has_class("word-active"):
                adj = self._refs_scroller.get_vadjustment()
                alloc = child.get_allocation()
                page_h = adj.get_page_size()
                cur = adj.get_value()
                if alloc.y < cur:
                    adj.set_value(alloc.y)
                elif alloc.y + alloc.height > cur + page_h:
                    adj.set_value(alloc.y + alloc.height - page_h + 8)
                break

    def _refresh_refs(self):
        """Re-render the Resources panel for the current tab + verse."""
        if self._refs_overlay is None or not self._refs_overlay.get_visible():
            return
        for child in self._refs_body.get_children():
            self._refs_body.remove(child)

        counts = self._get_tab_data_counts()
        for key, btn in self._ref_tab_buttons.items():
            btn.set_visible(counts.get(key, 0) > 0)
        order = [k for k in self._ref_tab_buttons if counts.get(k, 0) > 0]
        if self._refs_tab not in order:
            self._refs_tab = order[0] if order else "notes"
        self._sync_ref_tab_buttons()
        if not order:
            self._hide_refs()
            return

        tab = self._refs_tab
        verse = getattr(self, "_current_verse", 0)
        book_name = self.chapters[self.chapter_index][0] if 0 <= self.chapter_index < len(self.chapters) else ""

        if tab in ("notes", "crossrefs"):
            self.refs_loc.set_text(f"{book_name} · v. {verse}" if verse else book_name)
            if self._refs_pinned is not None and tab == "notes":
                entries = [self._refs_pinned]
            elif verse and self.source:
                want = "crossrefs" if tab == "crossrefs" else "notes"
                allrefs = self.source.refs(self.chapter_index)
                entries = [e for e in allrefs.get(verse, []) if e.get("kind", "notes") == want]
            else:
                entries = []
            if not entries:
                self._ref_placeholder(
                    "No references for this verse." if verse else "Select a verse with j/k."
                )
            else:
                for e in entries:
                    self._ref_card(e.get("label", ""), e.get("text", ""))
        else:
            intro, images, links = self.source.resources(self.chapter_index) if self.source else ("", [], [])
            self.refs_loc.set_text(book_name)
            if tab == "intro":
                if intro.strip():
                    self._ref_card("", intro)
                else:
                    self._ref_placeholder("No introduction for this book.")
            elif tab == "images":
                if not images:
                    self._ref_placeholder("No images for this book.")
                for im in images:
                    cap = Gtk.Label(label=im.get("caption") or os.path.basename(im["path"]))
                    cap.set_halign(Gtk.Align.START)
                    cap.set_xalign(0.0)
                    cap.set_line_wrap(True)
                    self._refs_body.pack_start(cap, False, False, 0)
                    btn = Gtk.Button()
                    try:
                        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(im["path"], 520, 400, True)
                        btn.add(Gtk.Image.new_from_pixbuf(pix))
                    except Exception:
                        btn.add(Gtk.Label(label="(image unavailable)"))
                    btn.connect(
                        "clicked",
                        lambda _b, p=im["path"]: self._open_uri("file://" + p),
                    )
                    self._refs_body.pack_start(btn, False, False, 0)
            elif tab == "links":
                if not links:
                    self._ref_placeholder("No external links for this book.")
                for lk in links:
                    btn = Gtk.LinkButton(uri=lk["url"], label=lk["text"] or lk["url"])
                    btn.set_halign(Gtk.Align.START)
                    self._refs_body.pack_start(btn, False, False, 0)
            elif tab == "word":
                lang = self._get_word_tab_label()
                self.refs_loc.set_text(f"{book_name} {lang}")
                bnum, cnum, verse = self._inspect_ref()
                words = lexicon.interlinear(bnum, cnum, verse) if bnum else []
                if not words:
                    self._ref_placeholder(
                        "No interlinear data for this verse yet (only a sample "
                        "is bundled in data/lexicon)."
                    )
                else:
                    n = len(words)
                    selected = words[0].get("w", "") if n else ""
                    self._ref_placeholder(
                        f"{book_name} {cnum}:{verse}  "
                        f"\"{selected}\""
                    )
                    for i, w in enumerate(words):
                        self._word_row(w, i, i == 0)

        self._refs_overlay.show_all()
        self._refs_overlay.set_visible(True)
        for key, btn in self._ref_tab_buttons.items():
            btn.set_visible(counts.get(key, 0) > 0)
        self._position_refs_above_notes()
        self._sync_ref_tab_buttons()
        if tab == "word":
            GLib.idle_add(self._scroll_to_active_word)
    def _apply_refs_height(self, height):
        """Set the height of the references panel."""
        self._refs_panel_height = int(height)
        if self._refs_overlay is not None:
            self._refs_overlay.set_size_request(-1, self._refs_panel_height)
        self._position_refs_above_notes()
    def _grow_refs_height(self, amount=40):
        self._apply_refs_height(self._refs_panel_height + amount)
    def _shrink_refs_height(self, amount=40):
        self._apply_refs_height(max(140, self._refs_panel_height - amount))
