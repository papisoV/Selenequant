"""Generate a frozen field-name snapshot of all pydantic models in selene_space_types.

Output is consumed by apps/desktop/lib/types/__tests__/contract.test.ts to assert
that every pydantic field appears in the corresponding TypeScript interface.

Manual sync is the chosen type-sharing strategy (no codegen). This snapshot
makes drift detectable: when a pydantic field is added, this script must be
re-run and the matching TS interface updated, or the contract test fails.

Run: python packages/shared-types/scripts/generate_field_snapshot.py
Output: packages/shared-types/selene_space_types/_field_snapshot.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_type_hints

try:
    from selene_space_types import trading as trading_types
    from selene_space_types import latency as latency_types
except ImportError as exc:
    raise SystemExit(
        "Could not import selene_space_types. Run from repo root with "
        "packages/shared-types on the path."
    ) from exc


def _extract_fields(model_cls) -> list[str]:
    """Return the list of field names declared on a pydantic BaseModel."""
    try:
        return sorted(model_cls.model_fields.keys())
    except AttributeError:
        # Non-pydantic fallback (shouldn't happen for our types)
        return sorted(get_type_hints(model_cls).keys())


def _extract_enum_values(model_cls) -> list[str]:
    """Return the list of enum values for a str-Enum subclass."""
    return sorted(member.value for member in model_cls)


def _collect_models(module) -> dict[str, list[str]]:
    """Collect all pydantic BaseModel subclasses + str-Enum subclasses from a module."""
    models: dict[str, list[str]] = {}
    enums: dict[str, list[str]] = {}
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        # Pydantic v2 BaseModel
        if isinstance(attr, type) and hasattr(attr, "model_fields") and attr.__module__ == module.__name__:
            models[attr_name] = _extract_fields(attr)
        # str Enum
        elif isinstance(attr, type) and hasattr(attr, "__members__") and attr.__module__ == module.__name__:
            enums[attr_name] = _extract_enum_values(attr)
    return {"models": models, "enums": enums}


def main() -> int:
    snapshot = {
        "trading": _collect_models(trading_types),
        "latency": _collect_models(latency_types),
    }

    out_path = Path(__file__).resolve().parents[1] / "selene_space_types" / "_field_snapshot.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Snapshot written to {out_path}")
    print(
        f"  trading: {len(snapshot['trading']['models'])} models, "
        f"{len(snapshot['trading']['enums'])} enums"
    )
    print(
        f"  latency: {len(snapshot['latency']['models'])} models, "
        f"{len(snapshot['latency']['enums'])} enums"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
