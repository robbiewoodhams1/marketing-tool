"""YouTube Data API v3 data-access layer.

The rest of the research system talks to `YouTubeClient` and receives plain
dataclasses; nothing outside this module needs to know the API's wire format.
Only the standard library is used for HTTP, and the transport is injectable so
tests never touch the network.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from research.config import Settings
from research.logging import get_logger

log = get_logger("research.youtube")

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
COMMENT_THREADS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"
WATCH_URL = "https://www.youtube.com/watch?v="
MAX_RESULTS_LIMIT = 50  # YouTube's per-page maximum for search.list
MAX_VIDEO_IDS_PER_REQUEST = 50  # videos.list accepts at most 50 ids per call
MAX_COMMENTS_PER_PAGE = 100  # commentThreads.list per-page maximum
COMMENT_ORDERS = ("relevance", "time")

# A transport takes a full URL and returns (http_status, response_body).
# It must raise YouTubeNetworkError if the request could not be completed.
Transport = Callable[[str], tuple[int, bytes]]


class YouTubeError(Exception):
    """Base class for all YouTube client failures."""


class YouTubeConfigError(YouTubeError):
    """No API key is configured."""


class YouTubeAuthError(YouTubeError):
    """The API key was rejected or is not permitted to use the API."""


class YouTubeQuotaError(YouTubeError):
    """Quota exhausted or request rate limited."""


class YouTubeAPIError(YouTubeError):
    """The API returned an unexpected HTTP error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class YouTubeNetworkError(YouTubeError):
    """The request could not reach the API (DNS, timeout, connection...)."""


class YouTubeResponseError(YouTubeError):
    """The API responded successfully but with an unexpected shape."""


class YouTubeCommentsUnavailableError(YouTubeError):
    """Comments cannot be read for a video: disabled, or the video is not found.

    An expected research condition rather than a failure of the API itself.
    """


@dataclass(frozen=True)
class YouTubeSearchResult:
    video_id: str
    url: str
    title: str
    channel_id: str
    channel_title: str
    published_at: datetime


@dataclass(frozen=True)
class YouTubeVideoMetadata:
    """Detailed video data from videos.list.

    Optional fields are None when YouTube omits them (e.g. hidden like counts,
    disabled comments); None is never the same as zero.
    """

    video_id: str
    url: str
    title: str
    channel_title: str
    published_at: datetime
    description: str | None = None
    duration: timedelta | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None


@dataclass(frozen=True)
class YouTubeComment:
    """A public comment. `type` is "top_level"; replies are not retrieved yet.

    `text` is the comment as plain text. Author details are deliberately not
    kept. `like_count` / `updated_at` are None if YouTube omits them.
    """

    video_id: str
    comment_id: str
    text: str
    published_at: datetime
    like_count: int | None = None
    updated_at: datetime | None = None
    type: str = "top_level"


@dataclass(frozen=True)
class YouTubeCommentPage:
    comments: list[YouTubeComment]
    next_page_token: str | None


def _urllib_transport(timeout: float) -> Transport:
    def fetch(url: str) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:  # non-2xx still carries a body
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Deliberately not chaining/including the URL: it contains the key.
            raise YouTubeNetworkError(
                f"Could not reach YouTube API: {type(exc).__name__}"
            ) from None

    return fetch


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_published(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class YouTubeClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        transport: Transport | None = None,
        timeout: float = 10.0,
    ):
        self._api_key = api_key or None
        self._transport = transport or _urllib_transport(timeout)

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> YouTubeClient:
        return cls(settings.youtube_api_key, **kwargs)

    def search_videos(
        self,
        query: str,
        *,
        max_results: int = 10,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
    ) -> list[YouTubeSearchResult]:
        self._require_key()
        if not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= max_results <= MAX_RESULTS_LIMIT:
            raise ValueError(f"max_results must be between 1 and {MAX_RESULTS_LIMIT}")

        params = {
            "part": "snippet",
            "type": "video",
            "q": query,
            "maxResults": str(max_results),
        }
        if published_after:
            params["publishedAfter"] = _rfc3339(published_after)
        if published_before:
            params["publishedBefore"] = _rfc3339(published_before)

        log.info("YouTube search started: query=%r max_results=%d", query, max_results)
        try:
            body = self._request(SEARCH_URL, params)
            results = self._parse_search(body)
        except YouTubeError as exc:
            log.error("YouTube search failed: %s: %s", type(exc).__name__, exc)
            raise
        log.info("YouTube search returned %d results", len(results))
        return results

    def get_videos(self, video_ids: list[str]) -> list[YouTubeVideoMetadata]:
        """Fetch metadata for up to 50 video ids in a single videos.list call.

        Unavailable (deleted/private) videos are simply absent from the result.
        Results follow the order in which YouTube returns them.
        """
        self._require_key()
        if not video_ids:
            raise ValueError("video_ids must not be empty")
        if len(video_ids) > MAX_VIDEO_IDS_PER_REQUEST:
            raise ValueError(
                f"at most {MAX_VIDEO_IDS_PER_REQUEST} video ids per request"
            )

        params = {"part": "snippet,contentDetails,statistics", "id": ",".join(video_ids)}
        log.info("YouTube metadata request started: %d ids", len(video_ids))
        try:
            body = self._request(VIDEOS_URL, params)
            videos = self._parse_videos(body)
        except YouTubeError as exc:
            log.error("YouTube metadata request failed: %s: %s", type(exc).__name__, exc)
            raise
        log.info("YouTube metadata request returned %d videos", len(videos))
        return videos

    def get_comment_threads(
        self,
        video_id: str,
        *,
        max_results: int = MAX_COMMENTS_PER_PAGE,
        page_token: str | None = None,
        order: str = "relevance",
    ) -> YouTubeCommentPage:
        """Fetch one page of top-level comments (commentThreads.list, 1 quota unit).

        Replies are not requested. Raises YouTubeCommentsUnavailableError if
        comments are disabled or the video is not found.
        """
        self._require_key()
        if not video_id.strip():
            raise ValueError("video_id must not be empty")
        if not 1 <= max_results <= MAX_COMMENTS_PER_PAGE:
            raise ValueError(f"max_results must be between 1 and {MAX_COMMENTS_PER_PAGE}")
        if order not in COMMENT_ORDERS:
            raise ValueError(f"order must be one of {COMMENT_ORDERS}")

        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": str(max_results),
            "order": order,
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token
        log.info(
            "YouTube comments request started: video=%s max_results=%d page_token=%s",
            video_id,
            max_results,
            "yes" if page_token else "no",
        )
        try:
            body = self._request(COMMENT_THREADS_URL, params)
            page = self._parse_comment_threads(body, video_id)
        except YouTubeCommentsUnavailableError as exc:
            log.warning("YouTube comments unavailable for %s: %s", video_id, exc)
            raise
        except YouTubeError as exc:
            log.error("YouTube comments request failed: %s: %s", type(exc).__name__, exc)
            raise
        log.info("YouTube comments request returned %d comments", len(page.comments))
        return page

    def _require_key(self) -> None:
        if not self._api_key:
            raise YouTubeConfigError(
                "YOUTUBE_API_KEY is not set; cannot call the YouTube API"
            )

    def _request(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        query = urllib.parse.urlencode({**params, "key": self._api_key})
        return self._get(f"{endpoint}?{query}")

    def _get(self, url: str) -> dict[str, Any]:
        status, raw = self._transport(url)
        if status != 200:
            raise self._error_for(status, raw)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise YouTubeResponseError("Response body was not valid JSON") from None
        if not isinstance(body, dict):
            raise YouTubeResponseError("Response JSON was not an object")
        return body

    @staticmethod
    def _error_for(status: int, raw: bytes) -> YouTubeError:
        message, reasons = f"HTTP {status}", []
        try:
            error = json.loads(raw)["error"]
            message = error.get("message") or message
            reasons = [e.get("reason", "") for e in error.get("errors", [])]
        except (ValueError, KeyError, TypeError, AttributeError):
            pass

        if {"commentsDisabled", "videoNotFound"}.intersection(reasons):
            return YouTubeCommentsUnavailableError(message)
        quota_reasons = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded",
                         "userRateLimitExceeded"}
        auth_reasons = {"keyInvalid", "keyExpired", "forbidden", "accessNotConfigured",
                        "ipRefererBlocked", "apiKeyInvalid"}
        if status == 429 or quota_reasons.intersection(reasons):
            return YouTubeQuotaError(f"Quota or rate limit exceeded: {message}")
        if (
            status == 401
            or auth_reasons.intersection(reasons)
            or "API key not valid" in message
        ):
            return YouTubeAuthError(f"Authentication failed: {message}")
        return YouTubeAPIError(f"YouTube API error: {message}", status=status)

    @staticmethod
    def _parse_search(body: dict[str, Any]) -> list[YouTubeSearchResult]:
        items = body.get("items")
        if not isinstance(items, list):
            raise YouTubeResponseError("Response is missing an 'items' list")
        results = []
        for item in items:
            try:
                video_id = item["id"]["videoId"]
                snippet = item["snippet"]
                results.append(
                    YouTubeSearchResult(
                        video_id=video_id,
                        url=f"{WATCH_URL}{video_id}",
                        title=html.unescape(snippet["title"]),
                        channel_id=snippet["channelId"],
                        channel_title=html.unescape(snippet["channelTitle"]),
                        published_at=_parse_published(snippet["publishedAt"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                raise YouTubeResponseError(
                    "Search result item had an unexpected shape"
                ) from None
        return results

    @staticmethod
    def _parse_videos(body: dict[str, Any]) -> list[YouTubeVideoMetadata]:
        items = body.get("items")
        if not isinstance(items, list):
            raise YouTubeResponseError("Response is missing an 'items' list")
        videos = []
        for item in items:
            try:
                video_id = item["id"]
                snippet = item["snippet"]
                content_details = item.get("contentDetails") or {}
                stats = item.get("statistics") or {}
                if not isinstance(video_id, str):
                    raise TypeError("video id is not a string")
                videos.append(
                    YouTubeVideoMetadata(
                        video_id=video_id,
                        url=f"{WATCH_URL}{video_id}",
                        title=html.unescape(snippet["title"]),
                        channel_title=html.unescape(snippet["channelTitle"]),
                        published_at=_parse_published(snippet["publishedAt"]),
                        description=snippet.get("description"),
                        duration=_parse_duration(content_details.get("duration")),
                        view_count=_parse_count(stats.get("viewCount")),
                        like_count=_parse_count(stats.get("likeCount")),
                        comment_count=_parse_count(stats.get("commentCount")),
                    )
                )
            except (KeyError, TypeError, ValueError, AttributeError):
                raise YouTubeResponseError(
                    "Video item had an unexpected shape"
                ) from None
        return videos

    @staticmethod
    def _parse_comment_threads(body: dict[str, Any], video_id: str) -> YouTubeCommentPage:
        items = body.get("items")
        if not isinstance(items, list):
            raise YouTubeResponseError("Response is missing an 'items' list")
        token = body.get("nextPageToken")
        if token is not None and not isinstance(token, str):
            raise YouTubeResponseError("nextPageToken was not a string")
        comments = []
        for item in items:
            try:
                # Only the thread's top-level comment; item["replies"] is ignored.
                top = item["snippet"]["topLevelComment"]
                snippet = top["snippet"]
                comment_id, text = top["id"], snippet["textDisplay"]
                if not isinstance(comment_id, str) or not isinstance(text, str):
                    raise TypeError("id/text is not a string")
                updated = snippet.get("updatedAt")
                comments.append(
                    YouTubeComment(
                        video_id=snippet.get("videoId") or video_id,
                        comment_id=comment_id,
                        text=html.unescape(text),
                        published_at=_parse_published(snippet["publishedAt"]),
                        like_count=_parse_count(snippet.get("likeCount")),
                        updated_at=_parse_published(updated) if updated else None,
                    )
                )
            except (KeyError, TypeError, ValueError, AttributeError):
                raise YouTubeResponseError(
                    "Comment thread item had an unexpected shape"
                ) from None
        return YouTubeCommentPage(comments, token or None)


_DURATION_RE = re.compile(
    r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$"
)


def _parse_duration(value: str | None) -> timedelta | None:
    """Parse an ISO 8601 duration such as PT1H2M3S. Missing -> None."""
    if value is None:
        return None
    match = _DURATION_RE.match(value)
    if not match or value in ("P", "PT"):
        raise ValueError(f"unparseable duration: {value!r}")
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def _parse_count(value: str | int | None) -> int | None:
    """YouTube returns counts as strings; absent means unknown, not zero."""
    return None if value is None else int(value)
