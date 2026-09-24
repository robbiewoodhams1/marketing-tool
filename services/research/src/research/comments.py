"""Public top-level comment retrieval for discovered videos.

Comments come from the official YouTube Data API (`commentThreads.list`) via
`YouTubeClient`, which owns the HTTP and wire format. This layer adds
validation, de-duplication, pagination with a hard per-video cap, and
failure semantics. Comments are raw evidence: no cleaning, ranking or analysis.
"""

from __future__ import annotations

from collections.abc import Iterable

from research.logging import get_logger
from research.youtube import (
    MAX_COMMENTS_PER_PAGE,
    YouTubeClient,
    YouTubeComment,
    YouTubeCommentsUnavailableError,
)

__all__ = ["YouTubeComments", "YouTubeComment", "MAX_COMMENTS_PER_VIDEO"]

log = get_logger("research.comments")

DEFAULT_MAX_COMMENTS_PER_VIDEO = 100
MAX_COMMENTS_PER_VIDEO = 1000  # guard against accidental unbounded quota use


class YouTubeComments:
    def __init__(self, client: YouTubeClient):
        self._client = client

    def get_comments(
        self,
        video_ids: Iterable[str],
        *,
        max_comments_per_video: int = DEFAULT_MAX_COMMENTS_PER_VIDEO,
        order: str = "relevance",
    ) -> list[YouTubeComment]:
        """Return top-level comments for the videos as one flat list.

        Videos appear in first-seen input order and each video's comments keep
        the order YouTube returned them in (`order` is "relevance" or "time");
        every comment carries its `video_id`. Ids are stripped and
        de-duplicated; blank ids and a `max_comments_per_video` outside
        1..1000 raise ValueError before any request. Empty input returns `[]`.

        Pages of up to 100 are fetched until the cap is reached or YouTube has
        no more; each page costs 1 quota unit. Videos with comments disabled,
        no comments, or not found contribute nothing and are logged. Other
        `YouTubeError`s (auth, quota, network, bad response) propagate.
        """
        if not 1 <= max_comments_per_video <= MAX_COMMENTS_PER_VIDEO:
            raise ValueError(
                f"max_comments_per_video must be between 1 and {MAX_COMMENTS_PER_VIDEO}"
            )
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
            "Comment retrieval started: %d videos, max %d per video",
            len(ordered),
            max_comments_per_video,
        )
        results: list[YouTubeComment] = []
        for video_id in ordered:
            results.extend(self._for_video(video_id, max_comments_per_video, order))
        log.info("Comment retrieval completed: %d comments", len(results))
        return results

    def _for_video(self, video_id: str, limit: int, order: str) -> list[YouTubeComment]:
        collected: list[YouTubeComment] = []
        token: str | None = None
        try:
            while len(collected) < limit:
                page = self._client.get_comment_threads(
                    video_id,
                    max_results=min(MAX_COMMENTS_PER_PAGE, limit - len(collected)),
                    page_token=token,
                    order=order,
                )
                collected.extend(page.comments[: limit - len(collected)])
                token = page.next_page_token
                if not token:
                    break
        except YouTubeCommentsUnavailableError:
            pass  # already logged by the client; expected, keep what we have
        if not collected:
            log.warning("No comments retrieved for %s", video_id)
        return collected
