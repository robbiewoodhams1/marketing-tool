"""Load a persisted Production and persist validated media-generation results.

Mirrors `research.production_store` and `research.opportunity_store`: writes
go through the `Database` protocol so tests never need Supabase. Unlike those
stages, one media RUN can legitimately have PARTIAL results - each scene is an
independent provider call (Part 3: scenes must be regeneratable one at a
time), so a transient failure on scene 4 must not discard scenes 1-3 that
already succeeded. `media_runs` and `media_assets` are therefore both
append-only (immutable, like every other analytical table here), but persisted
as a SEQUENCE of small atomic writes - one row each - rather than one
big all-or-nothing transaction: `save_media_run` (get-or-create, idempotent on
run_key) once, then one `save_media_asset` call per scene as it completes,
success or failure. A retry of the same run never re-calls the provider for a
scene that already has a completed asset (see `find_completed_asset`); a scene
whose only rows so far are failures is retried.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from research.logging import get_logger
from research.media_schema import MEDIA_ANALYSIS_TYPE, MEDIA_SCHEMA_VERSION, GeneratedAsset
from research.persistence import Database, PersistenceError

log = get_logger("research.media_store")

PRODUCTION_COLUMNS = (
    "id,production_run_id,opportunity_id,title,hook,target_audience,objective,platform,"
    "estimated_duration_seconds,scenes,script,cta,caption,hashtags,product_capability_refs,"
    "limitations,created_at"
)
PRODUCTION_RUN_COLUMNS = "id,product_key,product_version"
MEDIA_DIRECTION_COLUMNS = (
    "target_subject,appearance,setting,visual_style,camera_composition,lighting,avoid,"
    "custom_instructions,additional_direction"
)


@dataclass(frozen=True)
class ProductionRecord:
    """An existing `productions` row plus its run's product identity, as the
    media-generation engine needs it."""

    id: str
    product_key: str
    product_version: str
    row: Mapping[str, Any]  # the full row, exactly as stored


@dataclass(frozen=True)
class SaveRunOutcome:
    status: str  # "created" | "exists"
    run_id: str


def compute_run_key(
    *,
    production_id: str,
    image_provider: str | None,
    image_model: str | None,
    video_provider: str | None,
    video_model: str | None,
    voice_provider: str | None = None,
    voice_model: str | None = None,
    analysis_type: str = MEDIA_ANALYSIS_TYPE,
    pack_version: str,
    schema_version: str | None = None,
    media_direction: Mapping[str, Any] | None = None,
) -> str:
    """SHA-256 of the canonical run configuration.

    The production is immutable, so its id alone stands in for its content
    (same principle as Production's own `compute_run_key` for its
    opportunity). Anything that changes which provider/model would generate
    the scenes changes the key; nothing else does. Exactly one of
    (image_provider, image_model) / (video_provider, video_model) /
    (voice_provider, voice_model) is populated per run - a run is for one
    media type, never more than one (see research.media's own module
    docstring; research.voice mirrors the same rule for audio).

    `media_direction` is the snapshot from `MediaDirection.to_snapshot()`
    (or `None`): editing Media Direction changes what actually gets
    generated, so it must change the key too - otherwise a regeneration after
    an edit would be silently skipped as "already completed" by
    `find_completed_asset`, and the edit would have no effect. (Voice
    generation does not consult Media Direction - see research.voice's own
    module docstring - so this is always `None` for a voice run.)
    """
    identity = {
        "analysis_type": analysis_type,
        "production_id": production_id,
        "image_provider": image_provider,
        "image_model": image_model,
        "video_provider": video_provider,
        "video_model": video_model,
        "voice_provider": voice_provider,
        "voice_model": voice_model,
        "pack_version": pack_version,
        "schema_version": schema_version or MEDIA_SCHEMA_VERSION,
        "media_direction": media_direction,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_run_payload(
    *,
    production_id: str,
    image_provider: str | None,
    image_model: str | None,
    video_provider: str | None,
    video_model: str | None,
    voice_provider: str | None = None,
    voice_model: str | None = None,
    run_key: str,
    scene_count: int,
    manifest: dict[str, Any],
    pack_version: str,
    schema_version: str = MEDIA_SCHEMA_VERSION,
    media_direction: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "production_id": production_id,
        "image_provider": image_provider,
        "image_model": image_model,
        "voice_provider": voice_provider,
        "voice_model": voice_model,
        "video_provider": video_provider,
        "video_model": video_model,
        "analysis_type": MEDIA_ANALYSIS_TYPE,
        "pack_version": pack_version,
        "schema_version": schema_version,
        "run_key": run_key,
        "scene_count": scene_count,
        "input_manifest": manifest,
        "media_direction_snapshot": media_direction,
    }


class MediaRepository:
    def __init__(self, db: Database):
        self._db = db

    def get_production(self, production_id: str) -> ProductionRecord | None:
        rows = self._db.select("productions", PRODUCTION_COLUMNS, eq={"id": production_id})
        if not rows:
            return None
        row = rows[0]
        run_rows = self._db.select(
            "production_runs", PRODUCTION_RUN_COLUMNS, eq={"id": row["production_run_id"]}
        )
        if not run_rows:
            raise PersistenceError(
                f"production {production_id} references production_run "
                f"{row['production_run_id']}, which does not exist"
            )
        run = run_rows[0]
        return ProductionRecord(
            id=str(row["id"]), product_key=run["product_key"], product_version=run["product_version"], row=row
        )

    def get_media_direction(self, production_id: str) -> Mapping[str, Any] | None:
        """The saved `media_directions` row for this production, or `None` if
        none has ever been saved (equivalent to an empty `MediaDirection`).
        Editable, mutable, unlike everything else this repository reads."""
        rows = self._db.select("media_directions", MEDIA_DIRECTION_COLUMNS, eq={"production_id": production_id})
        return rows[0] if rows else None

    def find_run(self, run_key: str) -> str | None:
        """Id of an existing media run with this key, or None."""
        rows = self._db.select("media_runs", "id", eq={"run_key": run_key})
        return str(rows[0]["id"]) if rows else None

    def find_completed_asset(self, media_run_id: str, scene_number: int) -> bool:
        """True if this scene already has a completed asset for this run - a
        retry must not re-call the provider (and re-pay) for it."""
        rows = self._db.select(
            "media_assets", "id",
            eq={"media_run_id": media_run_id, "scene_number": scene_number, "status": "completed"},
        )
        return bool(rows)

    def save_run(self, payload: Mapping[str, Any]) -> SaveRunOutcome:
        """Get-or-create the run atomically. `exists` = an identical run was already stored."""
        result = self._db.rpc("save_media_run", {"payload": payload})
        if not isinstance(result, Mapping) or result.get("status") not in ("created", "exists"):
            raise PersistenceError(f"save_media_run returned an unexpected result: {result!r}")
        outcome = SaveRunOutcome(status=result["status"], run_id=str(result["run_id"]))
        log.info("Media run %s: %s", outcome.status, outcome.run_id)
        return outcome

    def save_asset(self, *, media_run_id: str, production_id: str, asset: GeneratedAsset) -> str:
        """Persist one scene's generation attempt (success or failure) as a new,
        immutable row. Returns the new asset id."""
        payload = {"media_run_id": media_run_id, "production_id": production_id, **asset.to_payload()}
        result = self._db.rpc("save_media_asset", {"payload": payload})
        if not isinstance(result, Mapping) or "asset_id" not in result:
            raise PersistenceError(f"save_media_asset returned an unexpected result: {result!r}")
        asset_id = str(result["asset_id"])
        log.info("Media asset saved: scene=%s status=%s id=%s", asset.scene_number, asset.status.value, asset_id)
        return asset_id
