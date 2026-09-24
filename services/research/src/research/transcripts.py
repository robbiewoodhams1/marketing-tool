"""Raw YouTube transcript retrieval.

Transcript text is NOT available from the official YouTube Data API v3 (its
`captions.download` needs OAuth as the video owner). Transcripts are fetched
through a `TranscriptProvider`; the default one (`research.transcript_provider`)
uses the third-party `youtube-transcript-api` package against YouTube's public
caption endpoints. Nothing outside this module and that adapter needs to know
which library is used.

This stage returns raw evidence only: caption wording is preserved, never
summarised or analysed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from research.logging import get_logger

log = get_logger("research.transcripts")

DEFAULT_LANGUAGE = "en"


class TranscriptError(Exception):
    """Base class for all transcript failures."""


class TranscriptConfigError(TranscriptError):
    """Invalid transcript configuration (e.g. a blank language)."""


class TranscriptUnavailableError(TranscriptError):
    """No transcript can be returned for a video. An expected research condition."""


class TranscriptsDisabledError(TranscriptUnavailableError):
    """The uploader disabled captions/transcripts for the video."""


class VideoUnavailableError(TranscriptUnavailableError):
    """The video does not exist, is private/removed, or cannot be played."""


class TranscriptLanguageUnavailableError(TranscriptUnavailableError):
    """Transcripts exist, but none in the requested language (and no fallback)."""


class TranscriptProviderError(TranscriptError):
    """The provider failed unexpectedly (blocked, unparsable, bad response...)."""


class TranscriptNetworkError(TranscriptError):
    """The provider could not reach YouTube (DNS, timeout, connection...)."""


@dataclass(frozen=True)
class RawTranscript:
    """What a provider returns: caption segment texts in playback order."""

    segments: list[str]
    language: str  # language code of the transcript actually chosen, e.g. "en-GB"
    is_generated: bool  # True for auto-generated (ASR) captions


class TranscriptProvider(Protocol):
    def fetch(self, video_id: str, language: str, allow_fallback: bool) -> RawTranscript:
        """Fetch the best transcript for `language` (matching e.g. "en" and "en-GB").

        If none matches: with `allow_fallback` use any other available
        transcript, otherwise raise TranscriptLanguageUnavailableError. Must
        raise only `TranscriptError` subclasses.
        """
        ...


@dataclass(frozen=True)
class YouTubeTranscript:
    video_id: str
    text: str
    language: str  # code of the transcript actually returned, may differ from requested
    is_generated: bool  # auto-generated captions are less accurate than manual ones


_WHITESPACE_RE = re.compile(r"\s+")


def _join_segments(segments: object) -> str:
    if not isinstance(segments, list) or not all(isinstance(s, str) for s in segments):
        raise TranscriptProviderError("Provider returned malformed transcript segments")
    parts = (_WHITESPACE_RE.sub(" ", s).strip() for s in segments)
    return " ".join(p for p in parts if p)


class YouTubeTranscripts:
    def __init__(
        self,
        provider: TranscriptProvider | None = None,
        *,
        language: str = DEFAULT_LANGUAGE,
        allow_fallback: bool = False,
    ):
        """
        `language` is the preferred language code (default "en", which also
        accepts regional variants such as "en-GB"). If it is unavailable for a
        video: with `allow_fallback=False` (default) the video is treated as
        unavailable; with True any other available transcript is used (manual
        preferred over auto-generated) and its actual code is in `.language`.
        """
        if not language or not language.strip():
            raise TranscriptConfigError("language must not be blank")
        if provider is None:
            from research.transcript_provider import YouTubeTranscriptApiProvider

            provider = YouTubeTranscriptApiProvider()
        self._provider = provider
        self._language = language.strip()
        self._allow_fallback = allow_fallback

    def get_transcript(self, video_id: str) -> YouTubeTranscript:
        """Fetch one transcript.

        Blank ids raise ValueError. Raises a `TranscriptUnavailableError`
        subclass (`TranscriptsDisabledError`, `VideoUnavailableError`,
        `TranscriptLanguageUnavailableError`) when there is no transcript, and
        `TranscriptProviderError` / `TranscriptNetworkError` on failures.
        """
        video_id = video_id.strip()
        if not video_id:
            raise ValueError("video id must not be empty")
        raw = self._provider.fetch(video_id, self._language, self._allow_fallback)
        text = _join_segments(raw.segments)
        if not text:
            raise TranscriptUnavailableError(f"Transcript for {video_id} is empty")
        if not isinstance(raw.language, str) or not raw.language:
            raise TranscriptProviderError("Provider returned no transcript language")
        return YouTubeTranscript(
            video_id=video_id,
            text=text,
            language=raw.language,
            is_generated=bool(raw.is_generated),
        )

    def get_transcripts(self, video_ids: Iterable[str]) -> list[YouTubeTranscript]:
        """Fetch transcripts for many videos, in first-seen input order.

        Ids are stripped and de-duplicated; blank ids raise ValueError before
        any request; empty input returns `[]`. Videos with no transcript
        (`TranscriptUnavailableError`) are logged and omitted, never invented:
        find them with `{i.strip() for i in ids} - {t.video_id for t in result}`.
        Provider and network errors are not expected per-video conditions and
        propagate, aborting the batch.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for raw in video_ids:
            video_id = raw.strip()
            if not video_id:
                raise ValueError("video ids must not be empty")
            if video_id not in seen:
                seen.add(video_id)
                ordered.append(video_id)

        if not ordered:
            return []

        log.info(
            "Transcript retrieval started: %d ids, language=%s fallback=%s",
            len(ordered),
            self._language,
            self._allow_fallback,
        )
        results: list[YouTubeTranscript] = []
        for video_id in ordered:
            try:
                results.append(self.get_transcript(video_id))
            except TranscriptUnavailableError as exc:
                log.warning(
                    "No transcript for %s: %s: %s", video_id, type(exc).__name__, exc
                )
            except TranscriptError as exc:
                log.error(
                    "Transcript retrieval failed for %s: %s: %s",
                    video_id,
                    type(exc).__name__,
                    exc,
                )
                raise
        log.info(
            "Transcript retrieval completed: %d of %d transcripts returned",
            len(results),
            len(ordered),
        )
        return results
