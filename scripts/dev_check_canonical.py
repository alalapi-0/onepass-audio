"""Quick helper to inspect canonical outputs under a given directory."""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path


def main(root: str) -> None:
    base = Path(root)
    pattern = str(base / "*.canonical.txt")
    for path in glob.glob(pattern):
        stem = os.path.basename(path).replace(".canonical.txt", "")
        text = Path(path).read_text(encoding="utf-8")
        print(f"[{stem}] canonical_len={len(text)} first50={text[:50]}")


if __name__ == "__main__":
    directory = sys.argv[1] if len(sys.argv) > 1 else r"E:\\onepass-audio\\out\\norm"
    main(directory)
