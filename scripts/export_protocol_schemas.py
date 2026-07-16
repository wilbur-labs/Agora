"""Export or verify checked-in Agora protocol JSON Schemas."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from agora.protocol.schema_registry import SCHEMA_MODELS, schema_document  # noqa: E402


def rendered_schema(name: str) -> str:
    return json.dumps(
        schema_document(name, SCHEMA_MODELS[name]),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if checked-in schemas do not match executable models",
    )
    args = parser.parse_args()
    output_dir = ROOT / "docs" / "architecture" / "schemas"
    output_dir.mkdir(parents=True, exist_ok=True)

    mismatches: list[str] = []
    for name in sorted(SCHEMA_MODELS):
        path = output_dir / f"{name}.schema.json"
        expected = rendered_schema(name)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                mismatches.append(path.relative_to(ROOT).as_posix())
        else:
            path.write_text(expected, encoding="utf-8", newline="\n")

    if mismatches:
        for path in mismatches:
            print(f"schema mismatch: {path}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
