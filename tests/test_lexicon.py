"""Headless tests for the offline lexicon (interlinear + Strong's)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lexicon  # noqa: E402


def run():
    assert lexicon.book_number("John") == 43
    assert lexicon.book_number("Genesis") == 1
    assert lexicon.book_number("Psalms") == 19
    assert lexicon.book_number("psalm") == 19

    assert lexicon.is_ot(1) and not lexicon.is_ot(43)

    gen = lexicon.interlinear(1, 1, 1)  # Genesis 1:1 Hebrew
    assert len(gen) == 7
    assert gen[0]["strongs"] == "H7225"
    assert lexicon.strongs("H7225")["gloss"]

    jn = lexicon.interlinear(43, 3, 16)  # John 3:16 Greek
    assert len(jn) >= 5
    agape = [w for w in jn if w["strongs"] == "G25"]
    assert agape and lexicon.strongs("G25")["gloss"]

    # unknown verse -> empty, no crash
    assert lexicon.interlinear(2, 1, 1) == []
    assert lexicon.strongs("Z999") is None
    assert lexicon.strongs(None) is None
    print("ALL LEXICON TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run())
