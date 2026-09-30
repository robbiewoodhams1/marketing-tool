"""Assembly record shape: what gets persisted for one assembly attempt.

Mirrors `research.media_schema` one layer up: Media Generation produces
independent per-scene assets; Assembly combines a production's already-
generated, already-validated scene VIDEOS into one finished MP4. It never
generates new media and never re-validates a scene's creative content - that
already happened upstream, in Production and Media Generation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

ASSEMBLY_ANALYSIS_TYPE = "media_assembly"
ASSEMBLY_SCHEMA_VERSION = "assembly-v1"

# The audio-assembly policy identifier folded into `compute_assembly_run_key`
# (see research.assembly_store) - so a FUTURE change to how audio is mixed
# (e.g. preserving source Veo audio, adding music/SFX, ducking) can never be
# silently treated as "the same assembly" as one produced under an older
# policy, even if the exact same video/voice source assets are reused.
#
#   ASSEMBLY_AUDIO_POLICY_MUTE       - Assembly V1: no audio track at all.
#   ASSEMBLY_AUDIO_POLICY_VOICEOVER  - Assembly V2 (research.assembly_v2):
#                                       voiceover is the sole, authoritative
#                                       spoken audio; source video audio
#                                       stays muted (see
#                                       research.ffmpeg_engine's module
#                                       docstring for the full sync policy).
ASSEMBLY_AUDIO_POLICY_MUTE = "mute-v1"
ASSEMBLY_AUDIO_POLICY_VOICEOVER = "voiceover-sync-v1"

# The caption policy identifier folded into `compute_assembly_run_key` (see
# research.assembly_store) exactly like ASSEMBLY_AUDIO_POLICY_* above - a
# future different caption style/timing/burn-in technique gets its own
# identifier so it can never be mistaken for "the same assembly" as one
# produced under today's policy. Unlike audio policy, captions are OPTIONAL:
# `None` (never a sentinel string) means "no captions were burned into this
# assembly" and is deliberately OMITTED from the run key entirely (see
# `compute_assembly_run_key`) so an assembly produced before Captions V1
# existed remains byte-identical (and therefore still reusable) to one
# produced today without requesting captions.
CAPTION_POLICY_BURNED_V1 = "captions-burned-v1"


class AssemblyStatus(str, Enum):
    """A row is written once and never updated (see the migration): a failed
    attempt is its own immutable row, not a mutated one - same principle as
    `research.media_schema.AssetStatus`."""

    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class AssemblyRecord:
    """One assembly attempt, in the shape `research.assembly_store` persists.

    Exactly one of (output_storage_path/output_storage_url, error_message) is
    meaningful, enforced by a DB CHECK constraint: a completed assembly
    always has an output reference and no error; a failed one always has an
    error and no output reference.
    """

    status: AssemblyStatus
    run_key: str
    source_asset_ids: tuple[str, ...]
    source_scene_count: int
    # The sum of the SOURCE VIDEO assets' own `duration_seconds` (the value
    # Media Generation actually got from the provider - never the
    # production's originally-requested scene duration). `None` if any
    # source asset's own duration is unknown, rather than a silently-partial
    # sum. Unaffected by Assembly V2's audio sync - see `scene_manifest` for
    # the actual, ffprobe-measured per-scene durations V2 synchronised to.
    source_duration_seconds: float | None
    # Independently measured (ffprobe) from the actual output file - never
    # assumed to equal the sum of source durations, since re-encoding can
    # shift exact timing slightly. `None` only when status is FAILED.
    output_duration_seconds: float | None = None
    output_storage_bucket: str | None = None
    output_storage_path: str | None = None
    output_storage_url: str | None = None
    output_mime_type: str | None = None
    error_message: str | None = None
    # Assembly V2 only (research.assembly_v2); empty for a V1, picture-only
    # assembly. The ordered source VOICEOVER asset ids, one per scene -
    # included in the run key precisely so a voiceover-only regeneration
    # (video unchanged) produces a genuinely new assembly, never silently
    # reused (see research.assembly_store.compute_assembly_run_key).
    source_voice_asset_ids: tuple[str, ...] = ()
    # Which audio-assembly policy produced this row - ASSEMBLY_AUDIO_POLICY_MUTE
    # (V1) or ASSEMBLY_AUDIO_POLICY_VOICEOVER (V2); see those constants above.
    audio_policy: str = ASSEMBLY_AUDIO_POLICY_MUTE
    # Assembly V2 only; empty for V1. One entry per scene, in scene order:
    # {scene_number, video_asset_id, voice_asset_id, video_duration_seconds,
    # voice_duration_seconds, scene_duration_seconds, sync_strategy} - the
    # actual, ffprobe-measured numbers `research.ffmpeg_engine` synchronised
    # to (see `SyncedSceneResult`), never the production's originally
    # requested scene durations. Kept as plain, extensible dicts (not their
    # own DB columns) so a future audio layer (ambient/music/SFX/ducking)
    # can add fields here without a schema migration.
    scene_manifest: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    # Captions V1 (research.captions / research.ffmpeg_engine.burn_captions).
    # `None` (the default) means captions were not requested for this
    # assembly - both fields are null-or-set together (enforced by a DB
    # CHECK): a caption manifest never exists without a policy naming what
    # produced it, and a policy is never set without the manifest it
    # produced. See `CAPTION_POLICY_BURNED_V1` above.
    caption_policy: str | None = None
    # One entry per caption segment, on the FINAL video's own timeline (see
    # `research.captions.caption_manifest_payload`) - the exact, reproducible
    # caption data, so it can be inspected/tested/reused without re-running
    # Assembly or re-measuring anything.
    caption_manifest: tuple[Mapping[str, Any], ...] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "run_key": self.run_key,
            "source_asset_ids": list(self.source_asset_ids),
            "source_scene_count": self.source_scene_count,
            "source_duration_seconds": self.source_duration_seconds,
            "output_duration_seconds": self.output_duration_seconds,
            "output_storage_bucket": self.output_storage_bucket,
            "output_storage_path": self.output_storage_path,
            "output_storage_url": self.output_storage_url,
            "output_mime_type": self.output_mime_type,
            "error_message": self.error_message,
            "source_voice_asset_ids": list(self.source_voice_asset_ids),
            "audio_policy": self.audio_policy,
            "scene_manifest": list(self.scene_manifest),
            "caption_policy": self.caption_policy,
            "caption_manifest": list(self.caption_manifest) if self.caption_manifest is not None else None,
        }
