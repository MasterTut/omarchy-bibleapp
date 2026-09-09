"""Keyboard dispatch mixin for OmarchyReader.

Extracts on_key_pressed_raw and helper predicates from main.py so the
central file stays focused on application lifecycle and window setup.
"""
from gi.repository import Gtk, Gdk


class KeybindingMixin:
    """Route keyboard events to the appropriate handler for the current focus."""

    def _hotkey_matches(self, binding, keyname, state):
        """Return True when a configured binding (e.g. "Ctrl+equal") matches."""
        if not binding:
            return False
        parts = [p.strip() for p in binding.split("+")]
        mods = [p.lower() for p in parts[:-1]]
        key = parts[-1]
        if keyname.lower() != key.lower():
            return False
        has_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        has_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if has_ctrl != ("ctrl" in mods):
            return False
        if has_shift != ("shift" in mods):
            return False
        return True

    def _focus_in_text_input(self):
        """True when keyboard focus is inside a text-entry widget."""
        widget = self.window.get_focus()
        while widget is not None:
            if isinstance(widget, (Gtk.Entry, Gtk.TextView)):
                return True
            widget = widget.get_parent()
        return False

    def _focus_in_webview(self):
        """True when keyboard focus is in the webview reader."""
        widget = self.window.get_focus()
        while widget is not None:
            if widget is self.webview:
                return True
            if widget is getattr(self, "_notes_overlay", None):
                return False
            if widget is getattr(self, "toc_overlay", None):
                return False
            widget = widget.get_parent()
        return False

    def on_key_pressed_raw(self, widget, event):
        from reader_config import HOTKEYS, SETTINGS

        keyname = Gdk.keyval_name(event.keyval)
        kn = keyname.lower() if keyname else ""
        state = event.state
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # Search overlay owns the keyboard when visible.
        if self._search_overlay is not None and self._search_overlay.get_visible():
            return False

        # Bookmark-symbol navigation mode.
        if self._bookmark_nav:
            return self._handle_bookmark_nav(kn, ctrl, shift)

        # "/" opens the go-to-passage search.
        if not ctrl and not shift and kn == "slash" and self.chapters:
            self._open_search()
            return True

        # Escape closes whichever overlay is open.
        if kn == "escape":
            return self._handle_escape()

        # Ctrl+ combos.
        if ctrl:
            return self._handle_ctrl(kn, keyname, shift, state)

        # Physical Home key returns to the library.
        if not ctrl and not shift and kn == "home":
            self.show_welcome()
            return True

        # TOC overlay navigation.
        if self.toc_overlay.get_visible():
            return self._handle_toc_nav(kn)

        # Home screen navigation.
        if self._on_home:
            return self._handle_home_nav(kn, ctrl, shift)

        # When Personal Space is focused in text input, let plain keys type.
        if self._focus == "notes" and self._focus_in_text_input():
            return False

        # Number keys switch tabs for the focused panel.
        if not ctrl and not shift and kn in ("1", "2", "3", "4", "5", "6"):
            return self._handle_tab_switch(kn)

        # Personal Space navigate mode.
        if self._focus == "notes" and not self._ps_editing:
            return self._handle_ps_nav(kn, ctrl, shift)

        # Resources panel navigation.
        if self._focus == "refs":
            return self._handle_refs_nav(kn)

        # Content focus: let j/k/h/l/arrows through to page JS.
        if self._focus == "content":
            return self._handle_content_keys(kn, ctrl, shift)

        # Fallback: paging keys in any non-content focus.
        return self._handle_paging(kn)

    def _handle_bookmark_nav(self, kn, ctrl, shift):
        if not ctrl and not shift:
            if kn in ("h", "left"):
                self._bm_nav(-1)
                return True
            if kn in ("l", "right"):
                self._bm_nav(1)
                return True
            if kn in ("return", "kp_enter"):
                self._bm_open()
                return True
            if kn == "x":
                self._bm_delete()
                return True
            if kn in ("escape", "q"):
                self._exit_bookmark_nav()
                self._focus_content()
                return True
        if ctrl and not shift and kn in ("j", "k"):
            self._exit_bookmark_nav()
            self._focus_content()
            return True
        return True

    def _handle_escape(self):
        if self._notes_overlay.get_visible():
            if self._ps_editing:
                self._exit_ps_edit()
            else:
                self._hide_notes()
            return True
        if self._refs_overlay is not None and self._refs_overlay.get_visible():
            self._hide_refs()
            return True
        if self._settings_overlay.get_visible():
            self._hide_settings()
            return True
        if self._help_overlay.get_visible():
            self._hide_help()
            return True
        if self.toc_overlay.get_visible():
            if self._toc_mode in ("chapters", "verses"):
                self._on_toc_back()
            else:
                self._hide_toc()
            return True
        return False

    def _handle_ctrl(self, kn, keyname, shift, state):
        from reader_config import HOTKEYS, SETTINGS

        if not shift and kn in ("1", "2", "3") and self._notes_overlay.get_visible():
            self._set_ps_tab({"1": "notes", "2": "prayer", "3": "memory"}[kn])
            return True
        if shift and kn in ("1", "2", "3", "4", "5", "6") and self._refs_overlay.get_visible():
            self._set_ref_tab({"1": "notes", "2": "crossrefs", "3": "intro",
                               "4": "images", "5": "links", "6": "word"}[kn])
            return True
        if not shift and kn == "r":
            self._toggle_refs()
            return True
        if not shift and kn == "i":
            self._on_import_epub()
            return True
        if shift and kn == "k":
            self._toggle_help()
            return True
        if shift and kn == "h":
            self._toggle_header()
            return True
        if shift and keyname in ("plus", "equal"):
            if self._focus_in_refs():
                self._grow_refs_height()
            else:
                self._grow_note_height()
            return True
        if shift and keyname in ("minus", "underscore"):
            if self._focus_in_refs():
                self._shrink_refs_height()
            else:
                self._shrink_note_height()
            return True
        if not shift and kn == "h":
            self._edit_from_list()
            return True
        if not shift and kn == "l":
            self._edit_from_list()
            return True
        if not shift and kn == "m":
            self._toggle_bookmark_current()
            return True
        if not shift and kn in ("j", "k"):
            if kn == "k" and self._focus == "content" and self._bm_buttons:
                self._enter_bookmark_nav()
                return True
            self._focus_next(forward=(kn == "j"))
            return True
        if self._hotkey_matches(HOTKEYS.get("toc"), keyname, state):
            self._toggle_toc()
            return True
        if self._hotkey_matches(HOTKEYS.get("note"), keyname, state):
            self._toggle_notes()
            return True
        if self._hotkey_matches(HOTKEYS.get("settings"), keyname, state):
            self._toggle_settings()
            return True
        if not shift and kn == "bracketleft":
            if self._ps_editing:
                self._exit_ps_edit()
            return True
        if self._hotkey_matches(HOTKEYS.get("toggle_reader_mode"), keyname, state):
            self._toggle_reader_mode()
            return True
        if self._hotkey_matches(HOTKEYS.get("font_increase"), keyname, state):
            self.change_font_size(self.font_size + 2)
            return True
        if self._hotkey_matches(HOTKEYS.get("font_decrease"), keyname, state):
            self.change_font_size(self.font_size - 2)
            return True
        if self._hotkey_matches(HOTKEYS.get("page_next"), keyname, state):
            self._run_js("nextPage();")
            return True
        if self._hotkey_matches(HOTKEYS.get("page_prev"), keyname, state):
            self._run_js("prevPage();")
            return True
        if kn == "o":
            if not self._on_home:
                self.on_open()
            return True
        return False

    def _handle_toc_nav(self, kn):
        if kn in ("down", "j"):
            self._toc_move(1)
            return True
        if kn in ("up", "k"):
            self._toc_move(-1)
            return True
        if kn in ("return", "kp_enter"):
            self._toc_activate_current()
            return True
        if kn in ("h", "left"):
            self._on_toc_back()
            return True
        if kn in ("l", "right"):
            return True
        return False

    def _handle_home_nav(self, kn, ctrl, shift):
        from reader_config import SETTINGS
        if SETTINGS.get("game_mode"):
            return self._handle_game_nav(kn, ctrl, shift)
        if kn == "j":
            self._home_move(1)
            return True
        if kn == "k":
            self._home_move(-1)
            return True
        if kn in ("return", "kp_enter"):
            self._home_activate()
            return True
        if kn == "i":
            self._on_import_epub()
            return True
        if kn == "x":
            self._home_delete()
            return True
        return False

    def _handle_game_nav(self, kn, ctrl, shift):
        if ctrl or shift:
            return False
        if kn in ("h", "left"):
            self._game_home_cycle(-1)
            return True
        if kn in ("l", "right", "j"):
            self._game_home_cycle(1)
            return True
        if kn == "k":
            self._game_home_cycle(-1)
            return True
        if kn in ("return", "kp_enter"):
            self._game_home_start()
            return True
        if kn == "i":
            self._on_import_epub()
            return True
        if kn == "x":
            self._home_delete()
            return True
        return False

    def _handle_tab_switch(self, kn):
        if self._focus == "refs":
            key = {"1": "notes", "2": "crossrefs", "3": "intro",
                   "4": "images", "5": "links", "6": "word"}.get(kn)
            if key:
                self._set_ref_tab(key)
                return True
        elif self._focus == "notes":
            key = {"1": "notes", "2": "prayer", "3": "memory", "4": "bookmarks"}.get(kn)
            if key:
                self._set_ps_tab(key)
                return True
        return False

    def _handle_ps_nav(self, kn, ctrl, shift):
        if getattr(self, "_ps_tab", "") == "bookmarks" and not ctrl and not shift:
            if kn in ("j", "down"):
                self._bm_list_move(1)
                return True
            if kn in ("k", "up"):
                self._bm_list_move(-1)
                return True
            if kn in ("return", "kp_enter"):
                self._bm_list_open()
                return True
            if kn == "x":
                self._bm_list_delete()
                return True
        if not ctrl and kn == "i":
            self._enter_ps_edit()
            return True
        if kn == "tab":
            self._cycle_ps_tab(-1 if shift else 1)
            return True
        if kn == "backtab":
            self._cycle_ps_tab(-1)
            return True
        if not ctrl and kn == "l":
            self._cycle_ps_tab(1)
            return True
        if not ctrl and kn == "h":
            self._cycle_ps_tab(-1)
            return True
        return False

    def _handle_refs_nav(self, kn):
        if kn in ("j", "down"):
            self._scroll_refs(1)
            return True
        if kn in ("k", "up"):
            self._scroll_refs(-1)
            return True
        if kn in ("space", "page_down"):
            self._scroll_refs(1, big=True)
            return True
        if kn == "page_up":
            self._scroll_refs(-1, big=True)
            return True
        if kn in ("l", "right"):
            self._set_ref_tab(self._next_ref_tab(1))
            return True
        if kn in ("h", "left"):
            self._set_ref_tab(self._next_ref_tab(-1))
            return True
        return False

    def _handle_content_keys(self, kn, ctrl, shift):
        if not ctrl and not shift and kn == "backspace":
            if self._restore_search_origin():
                return True
        if kn in ("j", "k", "up", "down", "h", "l", "left", "right"):
            return False
        if kn in ("page_down", "space"):
            self._run_js("nextPage();")
            return True
        if kn == "page_up":
            self._run_js("prevPage();")
            return True
        return False

    def _handle_paging(self, kn):
        if kn in ("h", "left"):
            self._run_js("prevPage();")
            return True
        if kn in ("l", "right"):
            self._run_js("nextPage();")
            return True
        if kn in ("page_down", "space"):
            self._run_js("nextPage();")
            return True
        if kn == "page_up":
            self._run_js("prevPage();")
            return True
        return False
