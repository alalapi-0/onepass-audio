import pathlib
import sys

from onepass.text_split import smart_split


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python scripts/dev_check_split.py <input.txt> <output.align.txt> [min_len] [max_len] [hard_max]")
        raise SystemExit(1)
    inp = pathlib.Path(sys.argv[1]).expanduser().resolve()
    outp = pathlib.Path(sys.argv[2]).expanduser().resolve()
    min_len = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    max_len = int(sys.argv[4]) if len(sys.argv) > 4 else 24
    hard_max = int(sys.argv[5]) if len(sys.argv) > 5 else 32
    text = inp.read_text(encoding="utf-8")
    lines, debug_rows = smart_split(
        text,
        min_len=min_len,
        max_len=max_len,
        hard_max=hard_max,
        return_debug=True,
    )
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dbg_path = outp.with_suffix(".debug.tsv")
    with dbg_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("idx\tlen\treason\ttext\n")
        for idx, (text, length, reason) in enumerate(debug_rows):
            preview = text.replace("\t", " ").replace("\n", " ")
            handle.write(f"{idx}\t{length}\t{reason}\t{preview}\n")
    print(f"lines={len(lines)} -> {outp}")


if __name__ == "__main__":
    main()
