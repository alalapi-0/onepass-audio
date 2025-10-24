#!/usr/bin/env python3
"""Render a configuration template by substituting environment variables.

This helper bridges CI/CD environments where secrets or per-device fields are
provided through environment variables.  Placeholders use ``${VAR}`` syntax and
are substituted using the current process environment.

Example::

    export WG_CLIENT_KEY="..."
    python3 core/tools/render_from_env.py \
        --template core/examples/minimal.json \
        --out /tmp/rendered.json \
        --force

Enable ``--strict`` to abort when a placeholder is left unresolved.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a template using environment variables.")
    parser.add_argument("--template", required=True, type=Path, help="Path to the template file.")
    parser.add_argument("--out", required=True, type=Path, help="Output path for the rendered file.")
    parser.add_argument("--force", action="store_true", help="Overwrite the output file if it exists.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if unresolved placeholders remain after substitution.",
    )
    return parser.parse_args(argv)


def substitute_env(text: str) -> str:
    """Substitute ${VAR} placeholders using the process environment."""

    def _replace(match: re.Match[str]) -> str:
        name = match.group("name")
        return os.environ.get(name, match.group(0))

    return PLACEHOLDER_RE.sub(_replace, text)


def ensure_no_placeholders(text: str) -> None:
    """Abort if *text* still contains ${VAR} markers."""

    remaining = PLACEHOLDER_RE.findall(text)
    if remaining:
        formatted = ", ".join(sorted(set(remaining)))
        raise SystemExit(f"Unresolved placeholders after substitution: {formatted}")


def render(template_path: Path, output_path: Path, force: bool, strict: bool) -> None:
    if output_path.exists() and not force:
        raise SystemExit(
            f"Refusing to overwrite existing file {output_path}. Use --force to replace it."
        )

    text = template_path.read_text(encoding="utf-8")
    rendered = substitute_env(text)

    if strict:
        ensure_no_placeholders(rendered)

    output_path.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    render(args.template, args.out, args.force, args.strict)
    print(f"Rendered template {args.template} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
