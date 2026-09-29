"""Is a generated asset consistent with the production it was built from?

Deterministic, in application code, never trusting whatever the provider
handed back:

* the asset's scene_number is a real scene in this production,
* the asset never cites a product capability beyond what that scene already
  used in Production (Media Generation cannot introduce a NEW capability
  claim - it can only depict what Production already decided and validated),
* a completed asset always has a storage reference and no error,
* a failed asset always has an error and no storage reference - so a failed
  generation can never be mistaken for a completed one.

What this does NOT do, deliberately (see Production V1's Part 13): inspect
the actual pixels/video for whether the generated image is faithful to the
prompt. That is not attempted in V1.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from research.media_schema import AssetStatus, GeneratedAsset, MediaValidationError


def validate_asset(asset: GeneratedAsset, *, production: Mapping[str, Any]) -> None:
    """Validate one asset against its production, or raise MediaValidationError."""
    scenes = production.get("scenes") or []
    by_number = {s["number"]: s for s in scenes}
    scene = by_number.get(asset.scene_number)
    if scene is None:
        raise MediaValidationError(
            f"scene_number {asset.scene_number} does not exist in this production "
            f"(has scenes {sorted(by_number)})",
            asset.scene_number,
        )

    allowed = set(scene.get("product_capability_refs") or [])
    extra = set(asset.product_capability_refs) - allowed
    if extra:
        raise MediaValidationError(
            f"asset cites capabilities {sorted(extra)} that scene {asset.scene_number} does not "
            "use in the production (product_capability_refs must stay within the scene's own)",
            asset.scene_number,
        )

    if asset.status is AssetStatus.COMPLETED:
        if not asset.storage_path or not asset.storage_url:
            raise MediaValidationError(
                "a completed asset must have a storage path and URL", asset.scene_number
            )
        if asset.error_message:
            raise MediaValidationError(
                "a completed asset must not carry an error_message", asset.scene_number
            )
    else:  # AssetStatus.FAILED
        if not asset.error_message:
            raise MediaValidationError("a failed asset must have an error_message", asset.scene_number)
        if asset.storage_path or asset.storage_url:
            raise MediaValidationError(
                "a failed asset must not have a storage reference", asset.scene_number
            )


def production_scene(production: Mapping[str, Any], scene_number: int) -> Mapping[str, Any] | None:
    """The one scene with this number, or None. A small shared lookup so
    callers (the CLI, tests) don't each re-derive it."""
    return next((s for s in (production.get("scenes") or []) if s["number"] == scene_number), None)
