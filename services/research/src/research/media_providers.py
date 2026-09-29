"""Provider abstractions for media generation.

`research.media` (the image/video service) depends only on `ImageProvider`
and `VideoProvider`; `research.voice` (the audio service) depends only on
`VoiceProvider`. None of them knows which vendor implements its provider -
that decision belongs entirely to whatever module builds the concrete
provider (`research.gemini.GeminiImageProvider`, `research.veo.VeoVideoProvider`,
`research.gemini_tts.GeminiVoiceProvider`; a different vendor for any of
these is purely additive later: a new module implementing the same
protocol, never a change to the calling service, the schema or the validator).

`VoiceProvider` is the first concrete member of the broader "AudioProvider"
family: image/video/audio are the three media types this schema
distinguishes (see `research.media_schema.AssetType`), and audio itself has
subtypes (voiceover, and in future ambient/music/SFX - see `AssetType.AUDIO`
and `research.media_schema.AudioSubtype`). A future `MusicProvider`/
`SfxProvider` would share `GeneratedAudio` as its result shape but take a
different `generate_*` signature suited to that content (e.g. musical
style/duration, not narration text) - there is deliberately no single
overarching `AudioProvider` Protocol class yet, since one would have no
second implementation to justify its exact shape until that day comes.

Result types are concrete dataclasses, not `Any`: the calling service needs
to know the shape of what a provider returns (raw bytes + a mime type, at
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
    and serve it. Never contains provider-specific structure beyond `metadata`.

    `cost_amount`/`cost_currency`: the provider's own estimate of what this
    one generation cost, computed by the provider from real data it has
    (e.g. actual token usage in its own response, times a published rate) -
    never a flat guess and never fabricated. Both are `None` together when no
    honest figure is available; a media service must never invent one itself.
    """

    data: bytes
    mime_type: str
    width: int | None = None
    height: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)  # raw-ish diagnostics, never huge blobs
    cost_amount: float | None = None
    cost_currency: str | None = None  # ISO 4217, e.g. "USD" - whatever currency the rate is actually denominated in


@dataclass(frozen=True)
class GeneratedVideo:
    """One generated video. Mirrors `GeneratedImage`, including the same
    `cost_amount`/`cost_currency` contract."""

    data: bytes
    mime_type: str
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    cost_amount: float | None = None
    cost_currency: str | None = None


@dataclass(frozen=True)
class GeneratedAudio:
    """One generated audio clip (voiceover in V1; ambient/music/SFX in
    future chapters). Mirrors `GeneratedImage`/`GeneratedVideo`'s shape and
    cost contract exactly - no width/height (meaningless for audio)."""

    data: bytes
    mime_type: str
    duration_seconds: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    cost_amount: float | None = None
    cost_currency: str | None = None


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


class VoiceProvider(Protocol):
    model: str  # stored with every asset generated through this provider

    def generate_voice(
        self, text: str, *, voice: str = "Kore", language: str = "en-US",
        speed: float | None = None, **options: Any,
    ) -> GeneratedAudio:
        """Render narration `text` EXACTLY as given - never rewritten,
        summarised, or embellished by this layer or its caller (see
        `research.voice`'s module docstring: Production's own approved
        script is the sole source of narration). Raises a
        `MediaProviderError` subclass on failure. `speed` is accepted for
        interface completeness; a concrete provider that documents no real
        speed control may simply ignore it rather than fake one."""
        ...
