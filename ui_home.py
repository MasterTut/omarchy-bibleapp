"""Home screen and GameMode welcome page mixin."""
import html
import json
import os


class HomeMixin:
    """Render the welcome / library / GameMode home screens."""

    def show_welcome(self, status_msg=None):
        from reader_config import SETTINGS, TRANSLATIONS_DIR, load_state, list_translations, _display_name

        self._exit_bookmark_nav()
        if self._bookmark_bar is not None:
            self._bookmark_bar.set_visible(False)
        if SETTINGS.get("game_mode"):
            self._show_game_home(status_msg)
            return
        self._is_loading = False
        self._show_spinner(False)
        self.loading_box.set_visible(False)
        self._hide_notes()
        if self._refs_overlay is not None:
            self._refs_overlay.set_visible(False)
        self._hide_settings()
        self._hide_help()
        if self._dock is not None:
            self._dock.set_visible(False)
        self._on_home = True
        self._home_options = []
        state = load_state()
        translations = list_translations()
        self._last_status = status_msg or ""

        # Continue-reading row.
        ans = ""
        home_idx = 0
        last_file = state.get("book")
        if last_file and os.path.exists(os.path.join(TRANSLATIONS_DIR, last_file)):
            self._home_options.append("continue")
            chap = state.get("chapter", 1)
            page = state.get("page", 1)
            name = _display_name(last_file)
            ans = f"""
            <div class="section continue">
              <div class="section-title">Continue where you left off</div>
              <a id="home-{home_idx}" class="continue-item" href="javascript:void(0)" data-action="continue">
                <span class="cont-book">{name}</span>
                <span class="cont-pos">Chapter {chap} · Page {page}</span>
                <span class="cont-arrow">&#10148;</span>
              </a>
            </div>"""
            home_idx += 1

        # Translation list.
        if translations:
            items = "".join(
                f'<a id="home-{home_idx + i}" class="book-item" href="javascript:void(0)" data-file="{t}">'
                f'<span class="book-name">{_display_name(t)}</span></a>'
                for i, t in enumerate(translations)
            )
            self._home_options.extend(translations)
            home_idx += len(translations)
        else:
            items = '<div class="empty">No translations found in the <code>translations/</code> folder. Place .epub files there.</div>'

        self._home_options.append("import")
        import_idx = home_idx
        self._home_sel = 0
        status_html = f'<div class="home-status">{html.escape(self._last_status)}</div>' if self._last_status else ""

        page_html = f"""<!doctype html><html><head><meta charset="utf-8">
{self._styles()}
<script>
function post(msg) {{
  if (window.webkit && window.webkit.messageHandlers &&
      window.webkit.messageHandlers.omarchy) {{
    try {{ window.webkit.messageHandlers.omarchy.postMessage(JSON.stringify(msg)); }}
    catch (e) {{}}
  }}
}}
document.addEventListener('click', function (e) {{
  var t = e.target.closest('[data-action]') || e.target.closest('[data-file]');
  if (!t) return;
  e.preventDefault();
  var action = t.getAttribute('data-action');
  if (action === 'continue') post({{type:'continue_reading'}});
  else if (action === 'import') post({{type:'import_epub'}});
  else post({{type:'open_book', file: t.getAttribute('data-file')}});
}});
</script>
</head><body class="home-body">
<div class="home">
  <div class="home-title">Omarchy&#8209;Bible</div>
  <div class="home-sub">Choose a Bible translation to begin.</div>
  {status_html}
  {ans}
  <div class="section">
    <div class="section-title">Bible Translations</div>
    <div class="book-list">{items}</div>
  </div>
  <div class="section">
    <div class="section-title">Library</div>
    <a id="home-{import_idx}" class="book-item" href="javascript:void(0)" data-action="import">
      <span class="book-name">Import EPUB</span>
    </a>
  </div>
   <div class="home-footer">j/k select · Enter open · i import · x remove · Ctrl+Shift+K keys</div>
 </div>
 </body></html>"""
        self.webview.load_html(page_html, None)
        self._focus = "content"
        self._update_header_focus()

    def _show_game_home(self, status_msg=None):
        """Zelda-style library: fires cycle translations, the sword continues."""
        from reader_config import TRANSLATIONS_DIR, list_translations, _display_name

        self._is_loading = False
        self._show_spinner(False)
        self.loading_box.set_visible(False)
        self._hide_notes()
        if self._refs_overlay is not None:
            self._refs_overlay.set_visible(False)
        self._hide_settings()
        self._hide_help()
        if self._dock is not None:
            self._dock.set_visible(False)
        self._on_home = True
        self._last_status = status_msg or ""

        translations = list_translations()
        self._home_options = list(translations)
        if not translations:
            self._game_sel = 0
        else:
            self._game_sel %= len(translations)
        self._home_sel = self._game_sel

        sel_file = translations[self._game_sel] if translations else ""
        name = _display_name(sel_file) if sel_file else ""
        sub = ("Press i to import an EPUB." if not translations
               else "Press Enter or click the sword to take this one.")
        status_html = f'<div class="game-status">{html.escape(self._last_status)}</div>' if self._last_status else ""

        flame_svg = '<svg class="flame" width="44" height="68" viewBox="0 0 40 60"><path d="M20 4 C 27 16 34 22 34 34 C 34 45 26 52 20 56 C 14 52 6 45 6 34 C 6 22 13 16 20 4 Z" fill="#e8a23b"/><path d="M20 18 C 23 26 28 30 28 36 C 28 43 24 48 20 51 C 16 48 12 43 12 36 C 12 30 17 26 20 18 Z" fill="#ffe9a8"/></svg>'
        sword = """<svg width="68" height="112" viewBox="0 0 24 40">
  <polygon points="12,0 15,17 9,17" fill="#cfd2d6"/>
  <line x1="12" y1="4" x2="12" y2="14" stroke="#8a8f96" stroke-width="0.8"/>
  <rect x="10.6" y="18" width="2.8" height="7" fill="#b08d3e"/>
  <rect x="6" y="17" width="12" height="2.2" fill="#d4af37"/>
  <circle cx="12" cy="27" r="1.7" fill="#d4af37"/>
</svg>"""

        page_html = f"""<!doctype html><html><head><meta charset="utf-8">
{self._styles()}
<script>
function post(msg) {{
  if (window.webkit && window.webkit.messageHandlers &&
      window.webkit.messageHandlers.omarchy) {{
    try {{ window.webkit.messageHandlers.omarchy.postMessage(JSON.stringify(msg)); }}
    catch (e) {{}}
  }}
}}
document.addEventListener('click', function (e) {{
  var t = e.target.closest('[data-action]');
  if (!t) return;
  e.preventDefault();
  var action = t.getAttribute('data-action');
  if (action === 'cycle') post({{type:'game_action', action:'cycle', dir: t.getAttribute('data-dir')}});
  else if (action === 'start') post({{type:'game_action', action:'start'}});
}});
</script>
</head><body class="home-body">
<div class="game">
  <div class="game-quote">
    <span class="t">IT&#8217;S DANGEROUS TO GO ALONE!</span>
    <span class="t">TAKE THIS.</span>
  </div>
  {status_html}
  <div class="game-cave">
    <div class="game-fire" data-action="cycle" data-dir="-1" title="Previous translation (h)">{flame_svg}</div>
    <a class="game-sword" href="javascript:void(0)" data-action="start" title="Continue reading">
      {sword}
      <span class="game-sword-label" id="game-name">{name}</span>
    </a>
    <div class="game-fire flame-r" data-action="cycle" data-dir="1" title="Next translation (l)">{flame_svg}</div>
  </div>
  <div class="game-sub">{sub}</div>
  <div class="game-footer">h / l cycle translation &#183; Enter sword continue &#183; i import &#183; x remove</div>
</div>
</body></html>"""
        self.webview.load_html(page_html, None)
        self._home_sel = self._game_sel
        self._focus = "content"
        self._update_header_focus()

    def _game_home_cycle(self, delta):
        from reader_config import list_translations, _display_name
        translations = list_translations()
        if not translations:
            return
        n = len(translations)
        self._game_sel = (self._game_sel + delta) % n
        self._home_sel = self._game_sel
        name = _display_name(translations[self._game_sel])
        js = "var e=document.getElementById('game-name');if(e)e.textContent=" + json.dumps(name) + ";"
        self._run_js(js)

    def _game_home_start(self):
        from reader_config import TRANSLATIONS_DIR, list_translations, load_state
        translations = list_translations()
        if not translations:
            return
        file = translations[self._game_sel % len(translations)]
        state = load_state()
        path = os.path.join(TRANSLATIONS_DIR, file)
        if state.get("book") == file:
            chapter = max(0, int(state.get("chapter") or 1) - 1)
            page = max(0, int(state.get("page") or 1) - 1)
            self.open_book(path, resume_index=chapter, resume_page_num=page)
        else:
            self.open_book(path)

    def _home_apply_highlight(self):
        if not self._on_home or not self._home_options:
            return
        idx = self._home_sel % len(self._home_options)
        target = f"#home-{idx}"
        js = (
            "var els=document.querySelectorAll('.focused');"
            "for(var i=0;i<els.length;i++)els[i].classList.remove('focused');"
            f"var t=document.querySelector('{target}');"
            "if(t)t.classList.add('focused');"
        )
        self._run_js(js)

    def _home_move(self, delta):
        if len(self._home_options) > 1:
            self._home_sel = (self._home_sel + delta) % len(self._home_options)
        self._home_apply_highlight()

    def _home_activate(self):
        import os
        from reader_config import TRANSLATIONS_DIR
        if not self._home_options:
            return
        idx = self._home_sel % len(self._home_options)
        sel = self._home_options[idx]
        if sel == "continue":
            self._continue_reading()
        elif sel == "import":
            self._on_import_epub()
        else:
            self.open_book(os.path.join(TRANSLATIONS_DIR, sel))

    def _home_delete(self):
        from reader_config import TRANSLATIONS_DIR
        if not self._home_options:
            return
        idx = self._home_sel % len(self._home_options)
        sel = self._home_options[idx]
        if sel in ("continue", "import"):
            return
        path = os.path.join(TRANSLATIONS_DIR, sel)
        try:
            if os.path.abspath(path) == os.path.abspath(self.book_path or ""):
                raise ValueError("Close the book before removing it.")
            os.remove(path)
            msg = f"Removed {sel}."
        except Exception as e:
            msg = f"Could not remove {sel}: {e}"
        self._home_sel = 0
        self.show_welcome(msg)
