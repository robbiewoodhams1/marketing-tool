"""Adapter from the `youtube-transcript-api` package to `TranscriptProvider`.

This is the only module that imports the third-party library. It maps the
library's exceptions onto the `research.transcripts` ones and implements
language selection.
"""

from __future__ import annotations

from typing import Any

import requests
from youtube_transcript_api import (
    AgeRestricted,
    InvalidVideoId,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeTranscriptApi,
    YouTubeTranscriptApiException,
)

from research.transcripts import (
    RawTranscript,
    TranscriptError,
    TranscriptLanguageUnavailableError,
    TranscriptNetworkError,
    TranscriptProviderError,
    TranscriptsDisabledError,
    VideoUnavailableError,
)


def _matches(code: str, language: str) -> bool:
    """"en" matches "en", "en-GB", "en-US"; "en-GB" matches only "en-GB"."""
    code, language = code.lower(), language.lower()
    return code == language or code.startswith(language + "-")


def _choose(transcripts: list[Any], language: str, allow_fallback: bool) -> Any | None:
    """Order: exact code, regional variant; manual before generated; then fallback."""

    def rank(t: Any) -> tuple[bool, bool]:
        return (t.is_generated, t.language_code.lower() != language.lower())

    matching = [t for t in transcripts if _matches(t.language_code, language)]
    if matching:
        return min(matching, key=rank)
    if allow_fallback and transcripts:
        return min(transcripts, key=lambda t: t.is_generated)
    return None


class YouTubeTranscriptApiProvider:
    def __init__(self, api: YouTubeTranscriptApi | None = None):
        self._api = api or YouTubeTranscriptApi()

    def fetch(self, video_id: str, language: str, allow_fallback: bool) -> RawTranscript:
        try:
            transcripts = list(self._api.list(video_id))
            chosen = _choose(transcripts, language, allow_fallback)
            if chosen is None:
                available = ", ".join(t.language_code for t in transcripts) or "none"
                raise TranscriptLanguageUnavailableError(
                    f"No {language!r} transcript for {video_id} (available: {available})"
                )
            fetched = chosen.fetch()
            return RawTranscript(
                segments=[s.text for s in fetched.snippets],
                language=fetched.language_code,
                is_generated=fetched.is_generated,
            )
        except TranscriptError:
            raise
        except TranscriptsDisabled:
            raise TranscriptsDisabledError(f"Transcripts are disabled for {video_id}") from None
        except NoTranscriptFound:
            raise TranscriptLanguageUnavailableError(
                f"No {language!r} transcript for {video_id}"
            ) from None
        except (VideoUnavailable, VideoUnplayable, AgeRestricted, InvalidVideoId) as exc:
            raise VideoUnavailableError(
                f"Video {video_id} is unavailable ({type(exc).__name__})"
            ) from None
        except requests.RequestException as exc:
            raise TranscriptNetworkError(
                f"Could not reach YouTube: {type(exc).__name__}"
            ) from None
        except YouTubeTranscriptApiException as exc:
            # Blocked IP, PO token required, unparsable page, HTTP failure...
            raise TranscriptProviderError(
                f"Transcript provider failed for {video_id}: {type(exc).__name__}"
            ) from None
        except Exception as exc:
            raise TranscriptProviderError(
                f"Unexpected transcript provider error: {type(exc).__name__}"
            ) from None
