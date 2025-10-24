#!/usr/bin/env python3
"""Validate PrivateTunnel client configuration files.

Usage examples::

    python3 core/tools/validate_config.py \
        --schema core/config-schema.json \
        --in core/examples/minimal.json

The command checks whether the JSON document adheres to the schema.  It exits
with code ``0`` on success and a non-zero code on failure.  Set ``--pretty`` to
print a redacted copy of the validated JSON for manual review.  Be careful when
handling private keys – the script never prints them unless explicitly
requested and even then they are partially masked.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

JSON = Dict[str, Any]


def _require_jsonschema() -> "jsonschema":
    """Import :mod:`jsonschema` and provide a helpful error if missing."""

    try:
        import jsonschema  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency hint
        message = (
            "Missing optional dependency 'jsonschema'.\n"
            "Install it with: pip install jsonschema\n"
            "The validation tools rely on the package but do not auto-install it."
        )
        raise SystemExit(message) from exc
    return jsonschema


def load_json_file(path: Path) -> JSON:
    """Read a JSON file using UTF-8 encoding."""

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"Input file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        pointer = f" (line {exc.lineno}, column {exc.colno})"
        raise SystemExit(f"Failed to parse JSON at {path}{pointer}: {exc.msg}") from exc


def mask_sensitive(data: JSON) -> JSON:
    """Return a copy of *data* with secrets such as private keys masked."""

    def _mask(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: _mask(_mask_private(key, val)) for key, val in value.items()}
        if isinstance(value, list):
            return [_mask(item) for item in value]
        return value

    def _mask_private(key: str, value: Any) -> Any:
        if isinstance(value, str) and key.lower() in {"private_key"}:
            if len(value) <= 8:
                return "***"
            return value[:4] + "…" + value[-4:]
        return value

    return _mask(deepcopy(data))


def validate_json(document: JSON, schema: JSON) -> None:
    """Validate *document* against *schema* and exit with contextual errors."""

    jsonschema = _require_jsonschema()

    try:
        jsonschema.Draft202012Validator(schema).validate(document)
    except jsonschema.exceptions.ValidationError as exc:  # type: ignore[attr-defined]
        location = "->".join(str(part) for part in exc.path) or "<root>"
        error = exc.message
        raise SystemExit(f"Validation error at {location}: {error}") from exc
    except jsonschema.exceptions.SchemaError as exc:  # type: ignore[attr-defined]
        raise SystemExit(f"Schema definition is invalid: {exc.message}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Configure and parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Validate a PrivateTunnel JSON configuration file against the schema.",
    )
    parser.add_argument(
        "--schema",
        required=True,
        type=Path,
        help="Path to the JSON schema describing the configuration format.",
    )
    parser.add_argument(
        "--in",
        dest="input_path",
        required=True,
        type=Path,
        help="Path to the JSON document to be validated.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Print a formatted (redacted) copy of the JSON document when validation succeeds.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    schema = load_json_file(args.schema)
    document = load_json_file(args.input_path)

    validate_json(document, schema)

    print(f"Validation succeeded for {args.input_path} against schema {args.schema}.")

    if args.pretty:
        redacted = mask_sensitive(document)
        formatted = json.dumps(redacted, indent=2, ensure_ascii=False)
        print("\nValidated document (redacted):\n" + formatted)

    return 0


if __name__ == "__main__":
    sys.exit(main())
