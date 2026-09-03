"""Gloss-based alignment of English verse words to the interlinear word list.

The interlinear datasets (MorphGNT / morphhb) carry no per-word English gloss,
so a perfect 1:1 translation mapping is impossible. Instead we align the English
content words of a verse to the original-language words using two signals:

1. Strong's *gloss* semantics (e.g. H1254's gloss "...create..." matches the
   English word "created" via a stem/prefix match). This handles the fact that
   Hebrew/Greek word order often differs from English (VSO vs SVO).
2. Order-preserving proportional position as a fallback when *no* interlinear
   word could be matched by gloss (e.g. glosses unavailable).

The result is a list of English-content-word indices parallel to the interlinear
list, so selecting the k-th original-language word can highlight the English
word that actually means it. Unmatched markers/particles map to -1 (no content
highlight, but they keep their position in the Resources list).

Kept GTK-free so the matcher is unit-testable.
"""
import re

# English function words that carry no original-language word of their own.
STOP_WORDS = frozenset(
    """the and of to in a an is that for was with as on be by at from this shall
    his upon it which he you i not but have had will they them their are were who
    unto said let there light divided called made even so thy""".split()
)

_ALPHA = re.compile(r"[A-Za-z]+")

_GLOSS_MEMO = {}


def _gloss_tokens(gloss):
    """Pull plausible English sense tokens out of a Strong's gloss string.

    "choose, create (creator), ... make (fat)." -> ["choose","create","creator",
    "make","fat"]  (short "[idiom] ..." markers and 1-letter words dropped)
    """
    if not gloss:
        return []
    text = gloss.replace("(", " ").replace(")", " ")
    text = re.sub(r"\[[^\]]*\]", " ", text)
    toks = []
    for w in _ALPHA.findall(text):
        w = w.lower()
        if len(w) >= 3 and w not in ("idiom", "very", "exceeding"):
            toks.append(w)
    return toks


def _stem_match(word, token):
    """True when two English words share a stem (prefix match, min length 3)."""
    n = min(len(word), len(token))
    if n < 3:
        return False
    return word[:n] == token[:n]


def non_stop_words(text):
    """English words of a verse text, lowercased, without function words."""
    out = []
    for w in _ALPHA.findall(text or ""):
        low = w.lower()
        if low not in STOP_WORDS:
            out.append(low)
    return out


def _gloss(code):
    if not code:
        return ""
    if code not in _GLOSS_MEMO:
        try:
            import lexicon
            entry = lexicon.strongs(code) or {}
            _GLOSS_MEMO[code] = entry.get("gloss", "")
        except Exception:
            _GLOSS_MEMO[code] = ""
    return _GLOSS_MEMO[code]


def align(english_words, interlinear):
    """Map interlinear index -> English-content index (-1 = no match).

    english_words  : list[str] lowercased English words (non_stop_words output).
    interlinear    : list[dict] with a "strongs" field ("H430"/"G2316") and
                     optionally "translit" (compatible with lexicon.interlinear).
    Returns a list parallel to ``interlinear``.
    """
    n_eng = len(english_words)
    if not interlinear:
        return []
    if not english_words:
        return [-1] * len(interlinear)

    # 1) Greedy gloss-based assignment.
    used = [False] * n_eng
    result = [-1] * len(interlinear)
    matched_any = False
    for i, wd in enumerate(interlinear):
        toks = _gloss_tokens(_gloss(wd.get("strongs", "")))
        if not toks:
            continue
        best, best_score = -1, -1.0
        for j in range(n_eng):
            if used[j]:
                continue
            w = english_words[j]
            if not any(_stem_match(w, t) for t in toks):
                continue
            exact = any(w == t for t in toks)
            score = (1.5 if exact else 1.0) - (j / max(n_eng, 1)) * 0.25
            if score > best_score:
                best_score, best = score, j
        if best >= 0:
            result[i] = best
            used[best] = True
            matched_any = True

    if matched_any:
        return result

    # 2) Fallback: order-preserving proportional position (no gloss data).
    for i in range(len(interlinear)):
        result[i] = round(i * (n_eng - 1) / max(len(interlinear) - 1, 1))
    return result
