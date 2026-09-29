"""Media asset shape: what gets persisted for one generated scene asset.

Unlike Synthesis/Opportunity/Production, Media Generation has no LLM-authored
structured output to strictly parse: the "creative" decisions were already
made and validated upstream (Production's scene fields). This module holds
the small, closed vocabulary (asset type, status) and the plain data shape
built deterministically by `research.media_pack` / `research.media`, not a
`from_dict` parser for untrusted model JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

MEDIA_ANALYSIS_TYPE = "media_generation"
MEDIA_SCHEMA_VERSION = "media-v1"


class MediaError(Exception):
    """Base class for media-generation failures."""


class MediaValidationError(MediaError, ValueError):
    """A media asset is not supported by the production/product context it was built from."""

    def __init__(self, message: str, scene_number: int | None = None):
        super().__init__(message if scene_number is None else f"scene {scene_number}: {message}")
        self.scene_number = scene_number


class AssetType(str, Enum):
    """Deliberately small; extend only when a provider for that type exists."""

    IMAGE = "image"
    VIDEO = "video"


class AssetStatus(str, Enum):
    """A row is written once and never updated (see the migration): a failed
    attempt is its own immutable row, not a mutated one. There is no
    "pending"/"in_progress" state in V1 - generation is synchronous."""

    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class GeneratedAsset:
    """One scene's generation attempt, in the shape `research.media_store` persists.

    Exactly one of (storage_path/storage_url, error_message) is meaningful,
    enforced by `research.media_validation` and by a DB CHECK constraint:
    a completed asset always has a storage reference and no error; a failed
    one always has an error and no storage reference.
    """

    scene_number: int
    asset_type: AssetType
    status: AssetStatus
    provider: str
    model: str
    prompt: str
    generation_config: dict[str, Any]
    product_capability_refs: tuple[str, ...]
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    storage_bucket: str | None = None
    storage_path: str | None = None
    storage_url: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    error_message: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "scene_number": self.scene_number,
            "asset_type": self.asset_type.value,
            "status": self.status.value,
            "provider": self.provider,
            "model": self.model,
            "prompt": self.prompt,
            "generation_config": self.generation_config,
            "product_capability_refs": list(self.product_capability_refs),
            "provider_metadata": self.provider_metadata,
            "storage_bucket": self.storage_bucket,
            "storage_path": self.storage_path,
            "storage_url": self.storage_url,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "error_message": self.error_message,
        }
