"""Unit tests for the gloss-based interlinear<->English alignment."""
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wordmatch


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


eng_gen1 = ["beginning", "god", "created", "heavens", "earth"]


def test_stop_words_filtered():
    got = wordmatch.non_stop_words(
        "In the beginning God created the heaven and the earth."
    )
    _assert(got == ["beginning", "god", "created", "heaven", "earth"], got)


def test_gloss_tokens():
    toks = wordmatch._gloss_tokens(
        "choose, create (creator), cut down, dispatch, do, make (fat)."
    )
    _assert("create" in toks and "make" in toks and "dispatch" in toks, toks)


def test_stem_match():
    _assert(wordmatch._stem_match("created", "create"), "created/create")
    _assert(wordmatch._stem_match("heavens", "heaven"), "heavens/heaven")
    _assert(not wordmatch._stem_match("god", "create"), "god/create")


def test_genesis_creation_god_aligned():
    # The user-reported swap: Hebrew is VSO ("created God"), English SVO
    # ("God created"). Gloss matching must map bara->created and elohim->God.
    inter = [
        {"strongs": "H7225", "translit": "reshiyth"},
        {"strongs": "H1254", "translit": "bara"},
        {"strongs": "H430", "translit": "elohim"},
        {"strongs": "H853", "translit": "eth"},
        {"strongs": "H8064", "translit": "shamayim"},
        {"strongs": "H853", "translit": "eth"},
        {"strongs": "H776", "translit": "erets"},
    ]
    mapping = wordmatch.align(eng_gen1, inter)
    _assert(mapping[1] == 2, mapping)   # created -> eng[created]
    _assert(mapping[2] == 1, mapping)   # God     -> eng[god]
    _assert(mapping[0] == 0, mapping)   # beginning
    _assert(mapping[3] == -1, mapping)  # marker (no english)
    _assert(mapping[4] == 3, mapping)   # heavens
    _assert(mapping[6] == 4, mapping)   # earth


def test_fallback_proportional():
    inter = [{"strongs": "X0001", "translit": "a"},
             {"strongs": "X0002", "translit": "b"},
             {"strongs": "X0003", "translit": "c"}]
    mapping = wordmatch.align(["alpha", "beta", "gamma", "delta"], inter)
    _assert(len(mapping) == 3 and all(i >= 0 for i in mapping), mapping)


if __name__ == "__main__":
    for name in sorted(list(globals())):
        if name.startswith("test_"):
            globals()[name]()
    print("ALL WORDMATCH TESTS PASSED")
