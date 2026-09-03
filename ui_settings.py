"""Settings + keybinding-help overlays mixin for the reader view."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from reader_config import SETTINGS, save_settings


class SettingsMixin:
    # ---------------- Settings ----------------
    def _build_settings_overlay(self):
        """Build the settings panel (opened with Ctrl+S)."""
        self._settings_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._settings_overlay.set_visible(False)
        self._settings_overlay.set_halign(Gtk.Align.CENTER)
        self._settings_overlay.set_valign(Gtk.Align.CENTER)
        self._settings_overlay.get_style_context().add_class("settings-overlay")
        self._settings_overlay.set_size_request(420, -1)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        bar.set_margin_top(14)
        bar.set_margin_bottom(8)

        title = Gtk.Label(label="Settings")
        title.get_style_context().add_class("title-label")
        title.set_halign(Gtk.Align.START)
        bar.pack_start(title, True, True, 0)

        close = Gtk.Button(label="\u2715")
        close.connect("clicked", lambda *_: self._hide_settings())
        bar.pack_end(close, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_start(16)
        row.set_margin_end(16)
        row.set_margin_top(8)
        row.set_margin_bottom(16)

        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl = Gtk.Label(label="Auto-hide top bar")
        lbl.set_xalign(0.0)
        lbl.set_halign(Gtk.Align.START)
        label_box.pack_start(lbl, False, False, 0)

        sub = Gtk.Label(label="Start with the header hidden; Ctrl+Shift+H shows it.")
        sub.get_style_context().add_class("progress-label")
        sub.set_xalign(0.0)
        sub.set_halign(Gtk.Align.START)
        label_box.pack_start(sub, False, False, 0)

        switch = Gtk.Switch()
        switch.set_active(SETTINGS.get("auto_hide_header", True))
        switch.set_halign(Gtk.Align.END)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("state-set", self._on_auto_hide_toggled)
        self._auto_hide_switch = switch

        row.pack_start(label_box, True, True, 0)
        row.pack_start(switch, False, False, 0)

        self._settings_overlay.pack_start(bar, False, False, 0)
        self._settings_overlay.pack_start(row, False, False, 0)

        row3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row3.set_margin_start(16)
        row3.set_margin_end(16)
        row3.set_margin_top(8)
        row3.set_margin_bottom(8)

        label_box3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl3 = Gtk.Label(label="Show Personal Space")
        lbl3.set_xalign(0.0)
        lbl3.set_halign(Gtk.Align.START)
        label_box3.pack_start(lbl3, False, False, 0)
        sub3 = Gtk.Label(
            label="Notes, Prayer Requests and Memorization (Ctrl+P). "
            "Turn off to hide the feature entirely."
        )
        sub3.get_style_context().add_class("progress-label")
        sub3.set_xalign(0.0)
        sub3.set_halign(Gtk.Align.START)
        sub3.set_line_wrap(True)
        label_box3.pack_start(sub3, False, False, 0)

        switch3 = Gtk.Switch()
        switch3.set_active(SETTINGS.get("show_personal_space", True))
        switch3.set_halign(Gtk.Align.END)
        switch3.set_valign(Gtk.Align.CENTER)
        switch3.connect("state-set", self._on_show_personal_space_toggled)
        self._show_ps_switch = switch3

        row3.pack_start(label_box3, True, True, 0)
        row3.pack_start(switch3, False, False, 0)

        self._settings_overlay.pack_start(row3, False, False, 0)

    def _on_show_personal_space_toggled(self, switch, active):
        SETTINGS["show_personal_space"] = bool(active)
        save_settings()
        if not active and self._notes_overlay is not None:
            self._hide_notes()
        return False

    def _on_auto_hide_toggled(self, switch, active):
        SETTINGS["auto_hide_header"] = bool(active)
        save_settings()
        # Apply immediately: when auto-hide is on, hide the header now;
        # when turned off, bring it back so state matches the setting.
        self.headerbar.set_visible(not active)
        return False

    def _toggle_settings(self):
        if self._settings_overlay.get_visible():
            self._hide_settings()
        else:
            self._show_settings()

    def _show_settings(self):
        self._hide_notes()
        self._hide_toc()
        self._hide_help()
        self._settings_overlay.show_all()
        self._settings_overlay.set_visible(True)
        self._auto_hide_switch.set_active(SETTINGS.get("auto_hide_header", True))

    def _hide_settings(self):
        self._settings_overlay.set_visible(False)

    # ---------------- Keybinding reference ----------------
    def _build_help_overlay(self):
        """Build the keybinding reference panel (opened with Ctrl+K)."""
        self._help_overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._help_overlay.set_visible(False)
        self._help_overlay.set_halign(Gtk.Align.CENTER)
        self._help_overlay.set_valign(Gtk.Align.CENTER)
        self._help_overlay.get_style_context().add_class("settings-overlay")
        self._help_overlay.set_size_request(460, -1)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        bar.set_margin_top(14)
        bar.set_margin_bottom(8)

        title = Gtk.Label(label="Keybindings")
        title.get_style_context().add_class("title-label")
        title.set_halign(Gtk.Align.START)
        bar.pack_start(title, True, True, 0)

        close = Gtk.Button(label="\u2715")
        close.connect("clicked", lambda *_: self._hide_help())
        bar.pack_end(close, False, False, 0)

        rows = [
            ("Ctrl + T", "Table of contents: books · chapters · verses (j/k, Enter, h/Back)"),
            ("Ctrl + P", "Personal Space: Notes · Prayer · Memory (tabs 1-3)"),
            ("/", "Search a book or passage (e.g. John 3:16) · Enter jumps there"),
            ("Ctrl + R", "Resources panel: Notes · Cross-refs · Intro · Images · Links"),
            ("1 – 3 / 1 – 5", "Switch tabs in the focused panel (Personal Space / Resources)"),
            ("Ctrl + j / k", "Move focus: content ⇄ personal space ⇄ resources"),
            ("j / k (resources)", "Scroll the Resources panel · h / l switch tabs"),
            ("Ctrl + h / l", "Focus the Personal Space notes editor"),
            ("Ctrl + Shift + H", "Toggle header bar"),
            ("Ctrl + S", "Settings"),
            ("Ctrl + Shift + K", "Keybindings reference"),
            ("Home", "Go to the library / choose a translation"),
            ("Ctrl + [", "Exit the notes editor back to tab select"),
            ("i (content)", "Word study: h/l cycle words, j/k verses, Word tab shows parse"),
            ("Ctrl + B", "Toggle reader mode"),
            ("Ctrl + I", "Import an EPUB into the library"),
            ("Ctrl + O", "Open a book file (in the reader)"),
            ("H / L / ← / →", "Previous / next page (left / right)"),
            ("J / K / ↑ / ↓", "Notes highlighted: move up / down · content: step verses (j/↓ down, k/↑ up)"),
            ("Library: j/k, i, x", "Move selection · i imports · x removes a translation"),
            ("x (Home)", "Delete the highlighted translation"),
            ("Ctrl + Shift + +/-", "Grow / shrink the focused panel (Personal Space / Resources)"),
            ("Ctrl + Right / Ctrl + Left", "Traverse chapters"),
        ]
        lines = "\n".join(
            f"<span weight='bold'>{k}</span>{'&#160;' * 4}{v}" for k, v in rows
        )
        body = Gtk.Label()
        body.set_markup(lines)
        body.set_xalign(0.0)
        body.set_halign(Gtk.Align.START)
        body.set_line_wrap(True)
        body.set_margin_start(24)
        body.set_margin_end(24)
        body.set_margin_bottom(20)

        self._help_overlay.pack_start(bar, False, False, 0)
        self._help_overlay.pack_start(body, False, False, 0)

    def _toggle_help(self):
        if self._help_overlay.get_visible():
            self._hide_help()
        else:
            self._show_help()

    def _show_help(self):
        self._hide_notes()
        self._hide_toc()
        self._hide_settings()
        self._help_overlay.show_all()
        self._help_overlay.set_visible(True)

    def _hide_help(self):
        self._help_overlay.set_visible(False)
