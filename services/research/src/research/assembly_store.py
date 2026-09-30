"""Load a production's completed scene VIDEO assets and persist Assembly
results.

Mirrors `research.media_store`: writes go through the `Database` protocol so
tests never need Supabase. Unlike Media Generation's per-scene sequence of
small writes, one Assembly attempt is a single all-or-nothing outcome (like
Synthesis/Opportunity/Production): there is nothing meaningful to persist
"partially" - either a finished MP4 exists or it doesn't - so a single
`save_assembly` call records the whole attempt, success or failure.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from research.assembly_schema import (
    ASSEMBLY_ANALYSIS_TYPE,
    ASSEMBLY_AUDIO_POLICY_MUTE,
    ASSEMBLY_SCHEMA_VERSION,
    AssemblyRecord,
)
from research.logging import get_logger
from research.media_store import MediaRepository, ProductionRecord
from research.persistence import Database, PersistenceError

log = get_logger("research.assembly_store")

SOURCE_ASSET_COLUMNS = (
    "id,scene_number,status,asset_type,storage_bucket,storage_path,storage_url,duration_seconds,created_at"
)
ASSEMBLY_COLUMNS = (
    "id,production_id,run_key,status,source_asset_ids,source_scene_count,source_duration_seconds,"
    "output_duration_seconds,output_storage_bucket,output_storage_path,output_storage_url,"
    "output_mime_type,error_message,source_voice_asset_ids,audio_policy,scene_manifest,"
    "caption_policy,caption_manifest,created_at,completed_at"
)


@dataclass(frozen=True)
class SourceVideoAsset:
    """The CURRENT completed video asset for one scene - the latest
    completed `media_assets` row for that scene number, across any media
    run (mirrors the dashboard's own "latest status per scene" logic in
    app/research/production/page.tsx)."""

    id: str
    scene_number: int
    storage_bucket: str
    storage_path: str
    storage_url: str
    duration_seconds: float | None


@dataclass(frozen=True)
class SourceVoiceAsset:
    """The CURRENT completed voiceover asset for one scene - same "latest
    completed row for that scene number" selection as `SourceVideoAsset`,
    scoped to `asset_type='audio', audio_subtype='voiceover'` (see
    `research.voice`)."""

    id: str
    scene_number: int
    storage_bucket: str
    storage_path: str
    storage_url: str
    duration_seconds: float | None


def compute_assembly_run_key(
    *,
    production_id: str,
    source_asset_ids: Sequence[str],
    source_voice_asset_ids: Sequence[str] = (),
    audio_policy: str = ASSEMBLY_AUDIO_POLICY_MUTE,
    caption_policy: str | None = None,
    schema_version: str = ASSEMBLY_SCHEMA_VERSION,
) -> str:
    """SHA-256 over the production id, the EXACT ORDERED sequence of source
    VIDEO asset ids, the EXACT ORDERED sequence of source VOICEOVER asset ids
    (Assembly V2 only - empty for a V1, picture-only assembly), the
    audio-assembly policy, and (if captions were requested) the caption
    policy that will be applied. Order matters (scene order is part of what
    makes one assembly identical to another); each asset id already uniquely
    pins the provider/model/content/direction used for that scene (see
    `research.media_store.compute_run_key`), so nothing else about an
    individual asset needs to be included - if any scene's underlying video
    OR voice asset changes (a regeneration), its id changes, and so does this
    key, producing a genuinely new assembly rather than a silently reused
    one. `audio_policy` is included so a FUTURE change to how audio is mixed
    (e.g. a v2 that also preserves source Veo audio) can never be mistaken
    for "the same assembly" as one produced under today's policy, even given
    the exact same source assets (see the `ASSEMBLY_AUDIO_POLICY_*` constants
    in `research.assembly_schema`).

    `caption_policy` defaults to `None` (no captions requested) and is then
    OMITTED from the hashed identity entirely, rather than hashed as a
    literal null - so an assembly computed without requesting captions
    produces EXACTLY the same key it always did, before Captions V1 existed
    (preserving every already-completed assembly's idempotency/discoverability;
    see `research.assembly_schema.CAPTION_POLICY_BURNED_V1`). Passing an
    actual policy string (captions requested) changes the key, as it must -
    the output genuinely differs."""
    identity: dict[str, Any] = {
        "production_id": production_id,
        "source_asset_ids": list(source_asset_ids),
        "source_voice_asset_ids": list(source_voice_asset_ids),
        "audio_policy": audio_policy,
        "schema_version": schema_version,
    }
    if caption_policy is not None:
        identity["caption_policy"] = caption_policy
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _latest_completed_asset_by_scene(
    db: Database, *, production_id: str, asset_type: str, audio_subtype: str | None = None
) -> list[Mapping[str, Any]]:
    """One row per scene number that has at least one completed asset of the
    given type (the latest such row, by `created_at`) - shared by
    `get_completed_video_assets` and `get_completed_voice_assets`. Scenes
    with none are simply absent from the result; the caller
    (`research.assembly`/`research.assembly_v2`) decides whether that is a
    hard failure. Returned sorted by scene number."""
    eq: dict[str, Any] = {"production_id": production_id, "asset_type": asset_type, "status": "completed"}
    if audio_subtype is not None:
        eq["audio_subtype"] = audio_subtype
    rows = db.select("media_assets", SOURCE_ASSET_COLUMNS, eq=eq)
    latest_by_scene: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        existing = latest_by_scene.get(row["scene_number"])
        if existing is None or row["created_at"] > existing["created_at"]:
            latest_by_scene[row["scene_number"]] = row
    return sorted(latest_by_scene.values(), key=lambda r: r["scene_number"])


class AssemblyRepository:
    def __init__(self, db: Database):
        self._db = db
        self._media = MediaRepository(db)  # reused for get_production - no separate query logic

    def get_production(self, production_id: str) -> ProductionRecord | None:
        return self._media.get_production(production_id)

    def get_completed_video_assets(self, production_id: str) -> list[SourceVideoAsset]:
        """One entry per scene number that has at least one completed video
        asset (the latest such row); scenes with none are simply absent -
        the caller (`research.assembly`/`research.assembly_v2`) decides
        whether that is a hard failure. Ordered by scene number."""
        rows = _latest_completed_asset_by_scene(self._db, production_id=production_id, asset_type="video")
        return [
            SourceVideoAsset(
                id=str(row["id"]), scene_number=row["scene_number"], storage_bucket=row["storage_bucket"],
                storage_path=row["storage_path"], storage_url=row["storage_url"],
                duration_seconds=row.get("duration_seconds"),
            )
            for row in rows
        ]

    def get_completed_voice_assets(self, production_id: str) -> list[SourceVoiceAsset]:
        """One entry per scene number that has at least one completed
        voiceover asset (the latest such row, `asset_type='audio',
        audio_subtype='voiceover'` - see `research.voice`); scenes with none
        are simply absent - the caller (`research.assembly_v2`) decides
        whether that is a hard failure. Ordered by scene number. Never falls
        back to an older voice asset once a newer completed one exists -
        same "latest completed row wins" policy as
        `get_completed_video_assets`."""
        rows = _latest_completed_asset_by_scene(
            self._db, production_id=production_id, asset_type="audio", audio_subtype="voiceover"
        )
        return [
            SourceVoiceAsset(
                id=str(row["id"]), scene_number=row["scene_number"], storage_bucket=row["storage_bucket"],
                storage_path=row["storage_path"], storage_url=row["storage_url"],
                duration_seconds=row.get("duration_seconds"),
            )
            for row in rows
        ]

    def find_assembly(self, run_key: str) -> str | None:
        """Id of an existing COMPLETED assembly with this key, or None - a
        FAILED attempt never counts as "already done" and must not block a
        retry (same principle as
        `research.media_store.MediaRepository.find_completed_asset`). The
        database's own partial unique index (only over completed rows) is
        the final idempotency guard - see the migration."""
        rows = self._db.select("media_assemblies", "id", eq={"run_key": run_key, "status": "completed"})
        return str(rows[0]["id"]) if rows else None

    def get_assembly(self, assembly_id: str) -> Mapping[str, Any] | None:
        rows = self._db.select("media_assemblies", ASSEMBLY_COLUMNS, eq={"id": assembly_id})
        return rows[0] if rows else None

    def save_assembly(self, *, production_id: str, record: AssemblyRecord) -> str:
        """Persist one assembly attempt (success or failure) as a new,
        immutable row. Returns the new assembly id."""
        payload = {
            "production_id": production_id, "analysis_type": ASSEMBLY_ANALYSIS_TYPE,
            "schema_version": ASSEMBLY_SCHEMA_VERSION, **record.to_payload(),
        }
        result = self._db.rpc("save_media_assembly", {"payload": payload})
        if not isinstance(result, Mapping) or "assembly_id" not in result:
            raise PersistenceError(f"save_media_assembly returned an unexpected result: {result!r}")
        assembly_id = str(result["assembly_id"])
        log.info("Media assembly saved: production=%s status=%s id=%s", production_id, record.status.value, assembly_id)
        return assembly_id
