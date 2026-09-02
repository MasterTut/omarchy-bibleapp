"""Pure string helpers: EPUB chapter HTML -> reader HTML."""
import html
import re


def extract_body(content):
    m = re.search(r"<body[^>]*>(.*?)</body>", content, re.S | re.I | re.DOTALL)
    body = m.group(1) if m else content

    # Drop anything that would inject its own colors/styles and override
    # the reader's theme: <style>, <link>, <base>, and inline style
    # attributes that set color or background.
    body = re.sub(r"<style[\s\S]*?</style>", "", body, flags=re.I)
    body = re.sub(r"<link\b[^>]*>", "", body, flags=re.I)
    body = re.sub(r"<base\b[^>]*/?>", "", body, flags=re.I)

    def neutral_style(attr):
        value = re.sub(
            r"([a-zA-Z-]*background[a-zA-Z-]*|color)\s*:\s*[^;\"']*;?",
            "",
            attr.group(1),
            flags=re.I,
        ).strip()
        if value:
            return 'style="' + value.rstrip("; ") + '"'
        return ""

    body = re.sub(r'style\s*=\s*"([^"]*)"', neutral_style, body, flags=re.I)
    body = re.sub(r"style\s*=\s*'([^']*)'", neutral_style, body, flags=re.I)
    return body


def annotate_verses(body):
    """Tag verse-number markers with data-vn so J/K can step per verse.

    Handles several publisher styles: plain <sup>N</sup> (KJV/ASV),
    <span class="bold">N </span> (ESV/EPUB), <span class="versenum">N</span>
    (Crossway), and other class-based verse markers. Only the style that
    actually appears is used.
    """
    # Plain <sup> numbers (KJV/ASV, many public-domain Bibles).
    body = re.sub(
        r"<sup[^>]*>(\d+)</sup>",
        r'<sup class="v" data-vn="\1">\1</sup>',
        body,
        flags=re.I,
    )
    def _tag_verse_span(match):
        inner = match.group(1)
        text = re.sub(r"<[^>]+>", "", inner)
        m = re.search(r"\d+", text)
        if not m:
            return match.group(0)
        vn = m.group(0)
        return f'<span class="v" data-vn="{vn}">{vn} </span>'

    if len(re.findall(r'class="v"', body)) < 3:
        # Known class-based verse markers (Crossway/ESV often use
        # class="versenum" or class="bold"). Allow nested tags like
        # <span class="bold"><big>1</big>:1 </span>.
        body = re.sub(
            r'<span\b[^>]*class="[^"]*(?:versenum|verse-num|verse|v|bold)[^"]*"[^>]*>(.*?)</span>',
            _tag_verse_span,
            body,
            flags=re.I | re.S,
        )
    if len(re.findall(r'class="v"', body)) < 3:
        # Generic span fallback: tag plain numeric spans only if there are
        # enough of them to look like verse numbers.
        candidates = re.findall(
            r'<span\b[^>]*>(.*?)</span>', body, flags=re.I | re.S
        )
        numbers = [re.search(r"\d+", c) for c in candidates]
        if len([n for n in numbers if n]) >= 3:
            body = re.sub(
                r'<span\b[^>]*>(.*?)</span>',
                _tag_verse_span,
                body,
                flags=re.I | re.S,
            )
    return body


def split_verses_into_lines(body):
    """Split paragraphs that contain multiple verses so each verse is on
    its own line. This makes j/k verse navigation feel like one line per
    verse, especially for publisher EPUBs that pack many verses into a
    single paragraph.
    """
    verse_span_pat = r'<span class="v" data-vn="(\d+)">\d+ </span>'

    def split_paragraph(match):
        p_open = match.group(1)
        p_content = match.group(2)
        markers = list(re.finditer(verse_span_pat, p_content))
        if len(markers) < 2:
            return match.group(0)
        cls_match = re.search(r'class="([^"]*)"', p_open)
        p_cls = cls_match.group(1) if cls_match else ""

        # Split the content by verse markers; keep any leading text before
        # the first marker attached to the first verse.
        chunks = re.split(verse_span_pat, p_content)
        lines = []
        for i in range(1, len(chunks), 2):
            vn = chunks[i]
            text = chunks[i + 1] if i + 1 < len(chunks) else ""
            if i == 1:
                text = chunks[0] + text
            text = text.strip()
            if not text:
                continue
            lines.append(
                f'<p class="verse-line {p_cls}">'
                f'<span class="v" data-vn="{vn}">{vn} </span>{text}</p>'
            )
        return "\n".join(lines) if lines else match.group(0)

    return re.sub(
        r'(<p\b[^>]*>)(.*?)(</p>)',
        split_paragraph,
        body,
        flags=re.I | re.S,
    )


def inject_first_verse(body):
    """Tag the implicit verse 1 in study Bibles where only the chapter number
    appears at the start of a chapter (no explicit '1' marker)."""
    if re.search(r'data-vn="1"', body):
        return body
    return re.sub(
        r'(<span class="chapter-num">\s*\d+\s*</span>)',
        r'\1<span class="v" data-vn="1">1 </span>',
        body,
        count=1,
        flags=re.I,
    )


def html_to_text(src):
    """Convert an HTML fragment to readable plain text with blank-line breaks."""
    t = re.sub(r"(?i)<(h[1-6])[^>]*>", "\n\n", src)
    t = re.sub(r"(?i)</(p|div|h[1-6]|li|blockquote|tr)>", "\n\n", t)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
