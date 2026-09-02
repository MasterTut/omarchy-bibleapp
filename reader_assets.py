"""Static web assets (CSS + reader JS) rendered inside the WebView."""

FONT_FAMILY = "JetBrainsMono Nerd Font"

STYLESHEET = """
<style>
  html, body {{
    margin: 0;
    padding: 0;
    background: {content_bg} !important;
    color: {foreground} !important;
    font-family: {font_family}, "JetBrains Mono", monospace;
    font-size: {font_size}px;
    line-height: 1.7;
    overflow: hidden;
    height: 100%;
  }}
  #container, .page, #source {{
    background: {content_bg} !important;
  }}
  #container {{
    color: {foreground} !important;
  }}
  #container p, #container span, #container li, #container td, #container div {{
    color: {foreground} !important;
  }}
  body {{
    box-sizing: border-box;
    padding: 0 {side_padding}px;
  }}
  h1, h2, h3, h4, h5, h6 {{
    color: {accent};
    line-height: 1.3;
    margin: 1.4em 0 0.6em;
  }}
  p {{ margin: 0 0 1.1em; }}
  a {{ color: {foreground}; text-decoration: underline; }}
  blockquote {{
    border-left: 3px solid {accent};
    margin: 1em 0;
    padding-left: 1em;
    color: {muted};
  }}
  img {{ max-width: 100%; height: auto; border-radius: 4px; }}
  code {{
    background: rgba(255,255,255,0.07);
    padding: 0.15em 0.4em;
    border-radius: 3px;
    font-size: 0.9em;
  }}
  pre {{
    background: rgba(255,255,255,0.05);
    padding: 1em;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 0.9em;
  }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{
    border: 1px solid rgba(255,255,255,0.15);
    padding: 0.4em 0.6em;
  }}
  hr {{ border: none; border-top: 1px solid rgba(255,255,255,0.2); margin: 1.5em 0; }}
  ::selection {{ background: {selection_background}; color: {selection_foreground}; }}

  .page {{
    position: absolute;
    left: 0;
    right: 0;
    top: 0;
    box-sizing: border-box;
    padding: {top_padding}px {side_padding}px {bottom_padding}px;
    opacity: 0;
    transition: opacity 0.15s ease;
  }}
  .page.active {{ opacity: 1; }}
  sup.v {{
    color: {accent};
    font-weight: bold;
    font-size: 0.72em;
    margin-right: 0.35em;
  }}
  span.v {{
    color: {accent};
    font-weight: bold;
    margin-right: 0.15em;
  }}
  .verse-line {{
    margin: 0.2em 0;
    line-height: 1.55;
  }}
  .v-highlight {{
    background: alpha({accent}, 0.55) !important;
    color: {foreground} !important;
    border-radius: 3px;
    padding: 0 2px;
  }}
  .verse-glow {{
    background: alpha({accent}, 0.10) !important;
    border-left: 3px solid {accent};
    padding-left: 0.6em;
  }}
  #container {{
    position: absolute;
    left: 0; right: 0; top: 0; bottom: 0;
    overflow: hidden;
  }}
  #loading-overlay {{
    position: fixed;
    left: 0; right: 0; bottom: 0;
    height: 3px;
    z-index: 9999;
    background: rgba(255,255,255,0.08);
    overflow: hidden;
  }}
  #loading-overlay .bar {{
    height: 100%;
    width: 30%;
    background: {accent};
    animation: loading-slide 1.2s ease-in-out infinite;
  }}
  @keyframes loading-slide {{
    0% {{ transform: translateX(-100%); }}
    100% {{ transform: translateX(400%); }}
  }}

  /* ---- Home / library screen ---- */
  body.home-body {{
    overflow: auto;
    padding: 0;
    background: {content_bg} !important;
  }}
  .home {{
    max-width: 760px;
    margin: 0 auto;
    padding: 56px {side_padding}px 80px;
  }}
  .home-title {{
    font-size: {home_title_fs}px;
    color: {accent};
    font-weight: bold;
    margin-bottom: 4px;
  }}
  .home-sub {{
    color: {muted};
    margin-bottom: 28px;
  }}
  .home-status {{
    background: alpha({accent}, 0.12);
    border: 1px solid {accent};
    border-radius: 8px;
    padding: 10px 14px;
    color: {foreground};
    margin-bottom: 20px;
  }}
  .section {{
    margin-bottom: 30px;
  }}
  .section-title {{
    font-size: {section_fs}px;
    color: {muted};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 12px;
    border-bottom: 1px solid rgba(255,255,255,0.12);
    padding-bottom: 6px;
  }}
  .book-list {{
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  .book-item {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 8px;
    color: {foreground};
    text-decoration: none;
    background: rgba(255,255,255,0.02);
    transition: background 0.15s ease, border-color 0.15s ease;
  }}
  .book-item:hover {{
    background: alpha({accent}, 0.12);
    border-color: {accent};
  }}
  .book-name {{
    font-size: {item_fs}px;
    font-weight: bold;
  }}
  .empty {{
    color: {muted};
    padding: 12px 2px;
  }}
  .continue {{
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 10px;
    padding: 14px 16px;
    background: alpha({accent}, 0.08);
  }}
  .continue .section-title {{
    border-bottom: none;
    margin-bottom: 6px;
  }}
  .continue-item {{
    display: flex;
    align-items: center;
    gap: 12px;
    color: {foreground};
    text-decoration: none;
  }}
  .cont-book {{
    font-weight: bold;
    font-size: {item_fs}px;
  }}
  .cont-pos {{
    color: {muted};
    font-size: 13px;
  }}
  .cont-arrow {{
    margin-left: auto;
    color: {accent};
    font-size: 18px;
  }}
  .home-footer {{
    margin-top: 34px;
    color: {muted};
    font-size: 12px;
    opacity: 0.7;
    text-align: center;
  }}
  .focused {{
    outline: 2px solid {accent};
    outline-offset: 2px;
  }}
</style>
"""

PAGE_JS = r"""
var state = { pages: 0, current: 0, ready: false, reported: false };

function post(msg) {
  if (window.webkit && window.webkit.messageHandlers &&
      window.webkit.messageHandlers.omarchy) {
    try { window.webkit.messageHandlers.omarchy.postMessage(JSON.stringify(msg)); }
    catch (e) {}
  }
}

function nodeToHtml(node) {
  if (node.nodeType === Node.TEXT_NODE) return node.nodeValue;
  return node.outerHTML;
}

function paginate() {
  var source = document.getElementById('source');
  var container = document.getElementById('container');
  if (!source || !container) return 0;

  var viewport = document.documentElement.clientHeight;
  var topPad = 60, bottomPad = 100, sidePad = 64;
  var pageHeight = viewport - topPad - bottomPad;
  if (pageHeight < 100) return 0;

  var width = document.body.clientWidth - sidePad * 2;
  if (width < 100) width = 600;

  var nodes = Array.prototype.slice.call(source.childNodes);
  var pieces = nodes.map(nodeToHtml).filter(function (s) { return s && s.trim(); });
  if (pieces.length === 0) { state.pages = 0; state.ready = true; return 0; }

  var probe = document.createElement('div');
  probe.className = 'page';
  probe.style.position = 'absolute';
  probe.style.left = '-99999px';
  probe.style.visibility = 'hidden';
  probe.style.boxSizing = 'border-box';
  probe.style.width = width + 'px';
  probe.style.padding = topPad + 'px ' + sidePad + 'px ' + bottomPad + 'px';
  document.body.appendChild(probe);

  function measure(html) {
    probe.innerHTML = html;
    return probe.scrollHeight;
  }

  var pieceLens = pieces.map(function (s) {
    var d = document.createElement('div');
    d.innerHTML = s;
    return d.textContent.length;
  });

  var pages = [];
  var charStarts = [];
  var block = '';
  var blockLen = 0;
  var total = 0;
  for (var i = 0; i < pieces.length; i++) {
    var candidate = block + pieces[i];
    if (block && measure(candidate) > pageHeight) {
      pages.push(block);
      charStarts.push(total);
      total += blockLen;
      block = pieces[i];
      blockLen = pieceLens[i];
    } else {
      block = candidate;
      blockLen += pieceLens[i];
    }
  }
  if (block) {
    pages.push(block);
    charStarts.push(total);
  }
  document.body.removeChild(probe);

  state.charStarts = charStarts;
  state.pages = pages.length;
  state.verseStarts = [];
  for (var p = 0; p < pages.length; p++) {
    var m = pages[p].match(/data-vn="(\d+)"/);
    state.verseStarts[p] = m ? parseInt(m[1], 10) : null;
  }
  container.innerHTML = '';
  for (var p = 0; p < pages.length; p++) {
    var div = document.createElement('div');
    div.className = 'page' + (p === 0 ? ' active' : '');
    div.innerHTML = pages[p];
    container.appendChild(div);
  }
  state.current = 0;
  state.ready = true;
  window.scrollTo(0, 0);
  return pages.length;
}

function currentCharStart(p) {
  return (state.charStarts && state.charStarts[p] != null) ? state.charStarts[p] : 0;
}

function report() {
  post({type:'ready', pages: state.pages, pch: currentCharStart(state.current)});
}

function currentPageIdx() { return state.current; }

function clearVerseHighlight() {
  var all = document.querySelectorAll('.verse-glow, .v-highlight');
  for (var i = 0; i < all.length; i++) {
    all[i].classList.remove('verse-glow');
    all[i].classList.remove('v-highlight');
  }
}

function resetVerseHighlight() {
  clearVerseHighlight();
  post({type:'verse', verse: 0});
}

function verseElems() {
  var p = document.querySelector('.page.active');
  if (!p) return [];
  return Array.prototype.slice.call(p.querySelectorAll('sup.v, span.v'));
}

function currentVerseEl() {
  var p = document.querySelector('.page.active');
  if (!p) return null;
  return p.querySelector('.v-highlight');
}

function applyVerseHighlight(el, vn) {
  clearVerseHighlight();
  if (!el) return;
  el.classList.add('v-highlight');
  var par = el.closest('p');
  if (par) par.classList.add('verse-glow');
  var page = el.closest('.page');
  if (page && page.scrollHeight > page.clientHeight) {
    page.scrollTop = Math.max(0, el.offsetTop - page.clientHeight * 0.35);
  }
  if (vn) post({type:'verse', verse: parseInt(vn, 10) || 0});
}

function moveVerse(delta) {
  var els = verseElems();
  if (els.length === 0) {
    scrollContent(delta > 0 ? 70 : -70);
    return false;
  }
  var cur = currentVerseEl();
  var idx = 0;
  if (cur) {
    var found = Array.prototype.indexOf.call(els, cur);
    if (found >= 0) idx = found + delta;
  }
  if (idx < 0) idx = 0;
  if (idx >= els.length) idx = els.length - 1;
  var el = els[idx];
  applyVerseHighlight(el, el.getAttribute('data-vn'));
  return true;
}

// Handle reader navigation directly in the page so it works even when the
// GTK key-press-event path misses the event.
document.addEventListener('keydown', function (e) {
  var tag = e.target.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  var key = e.key.toLowerCase();
  if (key === 'j' || key === 'arrowdown') {
    e.preventDefault();
    moveVerse(1);
    return;
  }
  if (key === 'k' || key === 'arrowup') {
    e.preventDefault();
    moveVerse(-1);
    return;
  }
  if (key === 'l' || key === 'arrowright') {
    e.preventDefault();
    nextPage();
    return;
  }
  if (key === 'h' || key === 'arrowleft') {
    e.preventDefault();
    prevPage();
    return;
  }
});

// Intercept clicks on reference/commentary markers (<a> with a title or an
// in-book #fragment) and send the note text to the Python panel instead of
// letting the webview navigate away.
document.addEventListener('click', function (e) {
  if (!e.target.closest) return;
  var a = e.target.closest('a[href]');
  if (!a) return;
  var href = a.getAttribute('href') || '';
  var title = a.getAttribute('title') || '';
  if (!title && href.indexOf('#') === -1) return;
  e.preventDefault();
  var label = (a.textContent || '').trim();
  var text = title;
  if (!text && href.indexOf('#') !== -1) {
    var frag = href.split('#').pop();
    try { frag = decodeURIComponent(frag); } catch (err) {}
    var t = document.getElementById(frag);
    if (t) {
      var box = t.closest ? (t.closest('p,li,div,blockquote') || t.parentNode) : t.parentNode;
      text = ((box || t).textContent || '').trim();
    }
  }
  post({type: 'ref', label: label, text: text});
});

function showPage(idx) {
  if (!state.ready || state.pages === 0) return 0;
  if (idx < 0) idx = 0;
  if (idx >= state.pages) idx = state.pages - 1;
  var pages = document.querySelectorAll('.page');
  for (var i = 0; i < pages.length; i++) {
    pages[i].className = i === idx ? 'page active' : 'page';
  }
  state.current = idx;
  window.scrollTo(0, 0);
  resetVerseHighlight();
  post({type:'page', cur: idx, pages: state.pages, pch: currentCharStart(idx)});
  return idx;
}

function scrollContent(delta) {
  var el = document.querySelector('.page.active');
  var cont = document.scrollingElement || document.documentElement;
  if (el && el.scrollHeight > el.clientHeight) {
    el.scrollTop += delta;
  } else if (cont) {
    cont.scrollTop += delta;
  }
}

function goToVerse(vn) {
  if (!state.ready || state.pages === 0) return false;
  vn = parseInt(vn, 10) || 0;
  if (vn <= 0) return false;
  var starts = state.verseStarts || [];
  var pageIdx = 0;
  for (var i = 0; i < starts.length; i++) {
    if (starts[i] != null && starts[i] <= vn) pageIdx = i;
  }
  var pages = document.querySelectorAll('.page');
  if (pages.length === 0) return false;
  for (var i = 0; i < pages.length; i++) {
    pages[i].className = i === pageIdx ? 'page active' : 'page';
  }
  state.current = pageIdx;
  window.scrollTo(0, 0);
  post({type:'page', cur: pageIdx, pages: state.pages, pch: currentCharStart(pageIdx)});
  var els = verseElems();
  var found = null;
  for (var i = 0; i < els.length; i++) {
    if (parseInt(els[i].getAttribute('data-vn'), 10) === vn) {
      found = els[i];
      break;
    }
  }
  if (found) {
    applyVerseHighlight(found, vn);
  } else {
    clearVerseHighlight();
    post({type:'verse', verse: 0});
  }
  return true;
}

function nextPage() {
  if (!state.ready) return -2;
  if (state.pages === 0) { post({type:'edge', dir:'next'}); return -1; }
  if (state.current + 1 < state.pages) {
    return showPage(state.current + 1);
  }
  post({type:'edge', dir:'next'});
  return -1;
}

function prevPage() {
  if (!state.ready) return -2;
  if (state.pages === 0) { post({type:'edge', dir:'prev'}); return -1; }
  if (state.current - 1 >= 0) {
    return showPage(state.current - 1);
  }
  post({type:'edge', dir:'prev'});
  return -1;
}

function gotoNextChapter() {
  post({type:'edge', dir:'next'});
}

function gotoPrevChapter() {
  post({type:'edge', dir:'prev'});
}

// Bootstrap: retry pagination until content lays out, then report exactly once.
(function () {
  var tries = 0, reported = false;
  function attempt() {
    if (reported) return;
    var n = 0;
    try { n = paginate(); } catch (e) { post({type:'jserror', msg: 'paginate: ' + e.message}); }
    if (n > 0) { reported = true; report(); return; }
    var src = document.getElementById('source');
    var empty = !src || src.childNodes.length === 0 ||
                (src.innerHTML && src.innerHTML.replace(/\s/g, '').length === 0);
    if (empty || tries >= 30) { reported = true; report(); return; }
    tries++;
    setTimeout(attempt, 120);
  }
  window.addEventListener('load', function () { setTimeout(attempt, 120); });
  window.addEventListener('resize', function () { setTimeout(attempt, 120); });
})();
"""

JS_HANDLER = "omarchy"
