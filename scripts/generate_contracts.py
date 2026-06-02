#!/usr/bin/env python3
"""
generate_contracts — emit JSON Schemas from Pydantic models into ./contracts/.

Python (packages/contracts) is the source of truth. The TypeScript client in
packages/tools-client imports the generated JSON; both sides agree because
they derive from the same models.

Run via `just gen-contracts`. Use `just gen-contracts-check` in CI to fail
if the checked-in JSON would drift from the models.

The repo's conftest adds packages/**/src to sys.path for tests; this script
replicates that dance so it works standalone under `uv run`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

# Mirror tests/conftest.py: the packages are organised under
# packages/*/src/<name>/ but aren't installed into site-packages. Pytest
# adds those paths via its conftest; we have to do it ourselves.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _src in (_REPO_ROOT / "packages").glob("**/src"):
    sys.path.insert(0, str(_src))
sys.path.insert(0, str(_REPO_ROOT))  # for `apps/...`
sys.path.insert(0, str(_REPO_ROOT / "packages"))  # for `adapters`, `features`

from pydantic import BaseModel  # noqa: E402

from contracts.gateway import BusEvent, DispatchRequest, ToolSpec  # noqa: E402

CONTRACTS_DIR = _REPO_ROOT / "contracts"
SchemaMode = Literal["validation", "serialization"]


def _schema(model: type[BaseModel], *, mode: SchemaMode = "validation") -> dict:
    """Return a JSON-Schema dict for the given Pydantic model. For our plain
    data models (no custom serializers / computed fields) `validation` and
    `serialization` modes produce the same output; we default to validation
    because that's the canonical Pydantic behaviour."""
    return model.model_json_schema(mode=mode)


# (filename, model, mode) — what we emit, in what shape, and how.
# `mode` matters when a model has serializers; today all three are plain data
# classes so the modes are equivalent, but recording the intent keeps the
# table honest as the surface grows.
EMIT: list[tuple[str, type[BaseModel], SchemaMode]] = [
    # GET /tools response: an array of ToolSpec. We emit the per-item shape
    # (so consumers reference ToolSpec) wrapped in the array envelope.
    ("tools.schema.json", ToolSpec, "validation"),
    # POST /dispatch request body.
    ("dispatch.schema.json", DispatchRequest, "validation"),
    # GET /stream SSE data line: one BusEvent per occurrence.
    ("events.schema.json", BusEvent, "serialization"),
]


def _wrap_array(item_schema: dict) -> dict:
    """Wrap a per-item schema in the array envelope the gateway actually
    returns for GET /tools. The single source remains `ToolSpec` — the array
    wrapper is just structural."""
    return {
        "type": "array",
        "items": item_schema,
        "title": "ToolCatalog",
    }


def generate(*, check: bool = False) -> int:
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    drift: list[str] = []
    for filename, model, mode in EMIT:
        raw = _schema(model, mode=mode)
        # `tools` is the only one we wrap — see EMIT notes above.
        new = _wrap_array(raw) if filename == "tools.schema.json" else raw
        path = CONTRACTS_DIR / filename
        old = json.loads(path.read_text()) if path.exists() else None
        if old != new:
            drift.append(filename)
            if not check:
                path.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n")
    if check:
        if drift:
            print(
                f"contracts drift: {', '.join(drift)}",
                file=sys.stderr,
            )
            print("Run `just gen-contracts` to update.", file=sys.stderr)
            return 1
        return 0
    if drift:
        print(f"wrote: {', '.join(drift)}")
    else:
        print("no changes")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--check",
        action="store_true",
        help="Fail (exit 1) if the generated files would change.",
    )
    args = p.parse_args()
    return generate(check=args.check)


if __name__ == "__main__":
    sys.exit(main())
