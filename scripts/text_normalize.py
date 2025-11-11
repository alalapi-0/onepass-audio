"""Text normalization helpers used by the CLI pipeline."""
from __future__ import annotations

import re

CJK = r"\u3400-\u9FFF\U00020000-\U0002FFFF"
CJK_CLASS = f"[{CJK}]"


def collapse_lines_preserve_spacing_rules(text: str) -> str:
    """Collapse newlines and tabs while preserving spacing rules.

    The behaviour follows the specification provided in the latest
    normalization prompt: newlines and tab characters are stripped without
    inserting spaces between CJK characters, while ASCII words keep at most one
    inter-word space.
    """

    if not text:
        return ""

    collapsed = text.replace("\t", "")
    collapsed = re.sub(r"\r?\n+", "", collapsed)
    collapsed = re.sub(r"[ \u00A0]+", " ", collapsed)
    collapsed = re.sub(fr"(?<={CJK_CLASS})\s+(?={CJK_CLASS})", "", collapsed)
    collapsed = re.sub(fr"(?<={CJK_CLASS})\s+(?=[A-Za-z0-9])", "", collapsed)
    collapsed = re.sub(fr"(?<=[A-Za-z0-9])\s+(?={CJK_CLASS})", "", collapsed)
    collapsed = re.sub(r"(?<=[A-Za-z0-9])\s+(?=[A-Za-z0-9])", " ", collapsed)
    return collapsed.strip()


__all__ = ["collapse_lines_preserve_spacing_rules", "CJK", "CJK_CLASS"]
