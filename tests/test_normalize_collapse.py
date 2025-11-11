import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onepass.normalize import collapse_soft_linebreaks


def t(s):
    return collapse_soft_linebreaks(s)


def test_chinese_join_no_space():
    assert t("人类\n如今") == "人类如今"
    assert t("中\n文ABC") == "中文ABC"
    assert t("ABC\n中文") == "ABC中文"


def test_ascii_word_break_inserts_space():
    assert t("Hello\nWorld") == "Hello World"
    assert t("v1.2\nbeta") == "v1.2 beta"
    assert t("GPU\n4090") == "GPU 4090"


def test_digits_and_cjk():
    assert t("2024\n年") == "2024年"
    assert t("第\n1章") == "第1章"


def test_tabs_and_multi_spaces():
    assert t("Hello\tWorld") == "Hello World"
    assert t("A  \n \t  B") == "A B"


def test_idempotent():
    s = "人\n类\n今\n日\nABC\nDEF"
    once = t(s)
    twice = t(once)
    assert once == twice
