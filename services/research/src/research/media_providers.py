"""Provider abstractions for media generation.

`research.media` (the media-generation service) depends only on `ImageProvider`
and `VideoProvider`. It never knows which vendor implements them - that
decision belongs entirely to whatever module builds the concrete provider
(`research.gemini.GeminiImageProvider` for V1; a video provider, another image
vendor, or a self-hosted model are all purely additive later: a new module
implementing the same protocol, never a change to the media service, the
schema or the validator).

Result types are concrete dataclasses, not `Any`: the media service needs to
know the shape of what a provider returns (raw bytes + a mime type, at
minimum) to persist and serve it, even though it must not know how those
bytes were produced.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


class MediaProviderError(Exception):
    """Base class for all media-provider failures."""


class MediaProviderConfigError(MediaProviderError):
    """The provider is not configured (e.g. no API key). Not a request/response failure."""


class MediaProviderNetworkError(MediaProviderError):
    """The request could not reach the provider (DNS, timeout, connection...)."""


class MediaProviderAPIError(MediaProviderError):
    """The provider's API returned an HTTP error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class MediaProviderResponseError(MediaProviderError):
    """The provider answered, but not with a usable asset.

    `raw` is kept for development diagnostics only (never persisted): it is
    deliberately excluded from the exception message so it never reaches logs.
    """

    def __init__(self, message: str, raw: Any = None):
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True)
class GeneratedImage:
    """One generated image, exactly as the media service needs it to persist
    and serve it. Never contains provider-specific structure beyond `metadata`."""

    data: bytes
    mime_type: str
    width: int | None = None
    height: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)  # raw-ish diagnostics, never huge blobs


@dataclass(frozen=True)
class GeneratedVideo:
    """One generated video. Mirrors `GeneratedImage`; no provider implements
    `VideoProvider` in V1 (see `research.gemini`'s module docstring)."""

    data: bytes
    mime_type: str
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ImageProvider(Protocol):
    model: str  # stored with every asset generated through this provider

    def generate_image(
        self, prompt: str, *, aspect_ratio: str = "9:16", **options: Any
    ) -> GeneratedImage:
        """Render one image from a prompt. Raises a `MediaProviderError` subclass on failure."""
        ...


class VideoProvider(Protocol):
    model: str  # stored with every asset generated through this provider

    def generate_video(
        self, prompt: str, *, aspect_ratio: str = "9:16", **options: Any
    ) -> GeneratedVideo:
        """Render one video from a prompt. Raises a `MediaProviderError` subclass on failure.

        No V1 implementation exists (see `research.gemini`): the protocol is
        defined now so a future provider is purely additive.
        """
        ...
