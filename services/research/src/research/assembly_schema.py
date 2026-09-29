"""Assembly record shape: what gets persisted for one assembly attempt.

Mirrors `research.media_schema` one layer up: Media Generation produces
independent per-scene assets; Assembly combines a production's already-
generated, already-validated scene VIDEOS into one finished MP4. It never
generates new media and never re-validates a scene's creative content - that
already happened upstream, in Production and Media Generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

ASSEMBLY_ANALYSIS_TYPE = "media_assembly"
ASSEMBLY_SCHEMA_VERSION = "assembly-v1"


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
    # The sum of the SOURCE assets' own `duration_seconds` (the value Media
    # Generation actually got from the provider - never the production's
    # originally-requested scene duration). `None` if any source asset's own
    # duration is unknown, rather than a silently-partial sum.
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
        }
