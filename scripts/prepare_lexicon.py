#!/usr/bin/env python3
"""Build the offline interlinear + Strong's datasets.

Reads raw open-source data (cloned under ``data/raw``) and writes the app's
``data/lexicon/{nt,ot,strongs}.json`` files.

Sources & licences (see README.md):
  * NT Greek text + morphology : MorphGNT SBLGNT (cc-by-sa 3.0; SBLGNT text EULA)
  * OT Hebrew text + morphology: OpenScriptures Hebrew Bible (WLC text PD,
    morphology cc-by 4.0) -> pre-built ``remapped.json`` via morphhbXML-to-JSON.py
  * Greek lemma -> Strong's    : jtauber/greek-lemma-mappings (cc-by-sa 4.0)
  * Strong's dictionaries      : openscriptures/strongs (Strong, 1890/1894; PD)

Output schema (see lexicon.py):
  * nt.json / ot.json : {"<book>:<chap>:<verse>": [word, ...]}
    word = {"w","lemma","translit","pos","strongs"}
  * strongs.json      : {"G1722": {"gloss","def"}, "H7225": {"gloss","def"}}
"""
import glob
import json
import os
import re
import sys
import unicodedata

import yaml

# MorphGNT/SBLGNT file prefix -> canonical NT book number (40..66).
SBLGNT_FILES = {
    "61": 40, "62": 41, "63": 42, "64": 43, "65": 44, "66": 45, "67": 46,
    "68": 47, "69": 48, "70": 49, "71": 50, "72": 51, "73": 52, "74": 53,
    "75": 54, "76": 55, "77": 56, "78": 57, "79": 58, "80": 59, "81": 60,
    "82": 61, "83": 62, "84": 63, "85": 64, "86": 65, "87": 66,
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "lexicon")

# --- Greek POS / parsing decoders (MorphGNT) ------------------------------
POS_LABEL = {
    "N-": "N", "V-": "V", "A-": "ADJ", "D-": "ADV", "C-": "CONJ", "P-": "PREP",
    "X-": "PRT", "I-": "INTJ", "RA": "T", "RD": "DPRON", "RR": "RPRON",
    "RP": "PPRON", "RI": "IPRON",
}
TENSE = {"P": "P", "I": "I", "F": "F", "A": "A", "X": "X", "Y": "Y"}
VOICE = {"A": "A", "M": "M", "P": "P"}
MOOD = {"I": "I", "D": "D", "S": "S", "O": "O", "N": "N", "P": "P"}
CASE = {"N": "N", "G": "G", "D": "D", "A": "A"}
NUMBER = {"S": "S", "P": "P"}
GENDER = {"M": "M", "F": "F", "N": "N"}


def greek_pos(pos_code, parse):
    """Normalise MorphGNT POS + parsing into a display string."""
    label = POS_LABEL.get(pos_code, pos_code.rstrip("-"))
    p = list(parse)
    if pos_code in ("V-",):
        # person(0) tense(1) voice(2) mood(3) case(4) number(5) gender(6) deg(7)
        person = p[0] if p[0] != "-" else ""
        tense = TENSE.get(p[1], "")
        voice = VOICE.get(p[2], "")
        mood = MOOD.get(p[3], "")
        number = NUMBER.get(p[5], "")
        detail = "%s%s%s-%s%s" % (tense, voice, mood, person, number)
        return "V-" + detail.rstrip("-")
    if pos_code in ("N-", "A-", "RA", "RD", "RR", "RP", "RI"):
        c = CASE.get(p[4], "")
        n = NUMBER.get(p[5], "")
        g = GENDER.get(p[6], "")
        detail = "".join(x for x in (c, n, g) if x)
        return ("%s-%s" % (label, detail)) if detail else label
    return label


# --- Greek lemma -> Strong's map ------------------------------------------
def build_greek_mapping():
    lexemes = yaml.safe_load(open(
        os.path.join(RAW, "greek-lemma-mappings", "lexemes.yaml"), encoding="utf-8")) or {}
    alt = yaml.safe_load(open(
        os.path.join(RAW, "greek-lemma-mappings", "alt_mapping.yaml"), encoding="utf-8")) or {}
    mapping = {}
    for lemma, info in lexemes.items():
        s = info.get("strongs") if isinstance(info, dict) else None
        if s:
            mapping[lemma] = "G%s" % s
    for form, target in alt.items():
        if isinstance(target, str) and target in mapping:
            mapping.setdefault(form, mapping[target])
    return mapping


# --- Strong's dictionaries -------------------------------------------------
def load_strongs_dicts():
    greek = json.load(open(os.path.join(RAW, "strongs", "greek", "strongs.json"), encoding="utf-8"))
    hebrew = json.load(open(os.path.join(RAW, "strongs", "hebrew", "strongs.json"), encoding="utf-8"))
    return greek, hebrew


def nfd(s):
    return unicodedata.normalize("NFD", s or "").lower().replace("\u00ef", "\u03b9")


def build_strongs_output(greek, hebrew):
    out = {}
    for code, e in greek.items():
        lemma = e.get("lemma", "")
        gloss = e.get("kjv_def") or (e.get("strongs_def") or "").split(";")[0] or ""
        deftxt = e.get("strongs_def") or e.get("kjv_def") or ""
        out[code] = {"gloss": gloss.strip(),
                     "def": ("%s — %s" % (lemma, deftxt)).strip() if deftxt else lemma}
    for code, e in hebrew.items():
        lemma = e.get("lemma", "")
        gloss = e.get("kjv_def") or (e.get("strongs_def") or "").split(";")[0] or ""
        deftxt = e.get("strongs_def") or e.get("kjv_def") or ""
        out[code] = {"gloss": gloss.strip(),
                     "def": ("%s — %s" % (lemma, deftxt)).strip() if deftxt else lemma}
    return out


# --- NT building -----------------------------------------------------------
def build_nt(greek_map, greek_dict):
    data = {}
    for path in glob.glob(os.path.join(RAW, "sblgnt", "*-morphgnt.txt")):
        base = os.path.basename(path).split("-")[0]
        book = SBLGNT_FILES.get(base)
        if not book:
            continue
        # index strongs_greek by normalized lemma for the fallback lookup
        for line in open(path, encoding="utf-8"):
            parts = line.split()
            if len(parts) < 7:
                continue
            c, v = int(parts[0][2:4]), int(parts[0][4:6])
            pos_code, parse = parts[1], parts[2]
            w = parts[4]          # text, punctuation stripped
            lemma = parts[6]
            strong = greek_map.get(lemma)
            translit = ""
            if strong:
                e = greek_dict.get(strong) or {}
                translit = e.get("translit", "")
            key = "%d:%d:%d" % (book, c, v)
            data.setdefault(key, []).append({
                "w": w,
                "lemma": lemma,
                "translit": translit,
                "pos": greek_pos(pos_code, parse),
                "strongs": strong or "",
            })
    return data


# --- OT building -----------------------------------------------------------
def ot_word(text, lemma, morph, hebrew_dict):
    """One OSHB word [text, lemma(strong), morph] -> app word dict."""
    codes = re.findall(r"H\d+", lemma or "")
    strong = codes[-1] if codes else ""
    e = hebrew_dict.get(strong) or {}
    w = text.replace("/", "")
    pos = ""
    if morph:
        pos = morph.split("/")[-1]
        pos = re.sub(r"^H", "", pos)
    return {
        "w": w,
        "lemma": e.get("lemma", ""),
        "translit": e.get("xlit", ""),
        "pos": pos,
        "strongs": strong,
    }


def build_ot(hebrew_dict):
    remapped = json.load(open(os.path.join(RAW, "morphhb", "remapped.json"), encoding="utf-8"))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from lexicon import BOOKS
    book_num = {name: i + 1 for i, name in enumerate(BOOKS[:39])}
    data = {}
    for book, chapters in remapped.items():
        num = book_num.get(book)
        if not num:
            continue
        for ci, verses in enumerate(chapters, start=1):
            for vi, words in enumerate(verses, start=1):
                key = "%d:%d:%d" % (num, ci, vi)
                data[key] = [ot_word(t, l, m, hebrew_dict) for (t, l, m) in words]
    return data


def main():
    os.makedirs(OUT, exist_ok=True)
    greek_map = build_greek_mapping()
    greek_dict, hebrew_dict = load_strongs_dicts()
    nt = build_nt(greek_map, greek_dict)
    ot = build_ot(hebrew_dict)
    strongs = build_strongs_output(greek_dict, hebrew_dict)
    for name, obj in (("nt.json", nt), ("ot.json", ot), ("strongs.json", strongs)):
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        print("%s: %d keys" % (name, len(obj)))
    # report coverage of greek strongs assignment
    missing = {k: [w for w in v if not w["strongs"]] for k, v in nt.items() if any(not w["strongs"] for w in v)}
    nwords = sum(len(v) for v in nt.values())
    nmissing = sum(len(v) for v in missing.values())
    print("NT words total=%d, without strongs=%d (%.2f%%)" % (nwords, nmissing, 100.0 * nmissing / nwords))
    print("Wrote lexicon to %s" % OUT)


if __name__ == "__main__":
    sys.exit(main())
