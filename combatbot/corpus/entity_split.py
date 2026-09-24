"""Deterministic layout coverage. Never uses image content or prediction scores."""
from __future__ import annotations

import hashlib
from combatbot.vision.coordinates import LayoutSignature


def layout_digest(signature: str | None) -> str:
    if not signature:
        return "UNKNOWN"
    if signature.startswith("{"):
        return LayoutSignature.from_json(signature).digest
    return signature


def cover_layouts(registry: dict, layouts: dict[str, set[str]]) -> dict:
    """Upgrade v1 without discarding history; TEST is immutable, including absent groups."""
    groups = dict(registry.get("groups", {}))
    history = list(registry.get("migrations", []))
    missing = []
    for layout, candidates in sorted(layouts.items()):
        if layout == "UNKNOWN" or any(groups.get(g) == "train" for g in candidates):
            continue
        eligible = [g for g in candidates if groups.get(g) in (None, "validation")]
        if not eligible:
            missing.append(layout)
            continue
        selected = min(eligible, key=lambda g: (
            0 if groups.get(g) == "validation" else 1,
            hashlib.sha256(f"{layout}|{g}".encode()).hexdigest(), g))
        history.append({"reason": "layout_train_coverage", "group_id": selected,
                        "previous_split": groups.get(selected), "new_split": "train",
                        "layout_signature": layout})
        groups[selected] = "train"
    return {**registry, "schema_version": 2, "groups": groups, "migrations": history,
            "layouts_without_train": missing}
