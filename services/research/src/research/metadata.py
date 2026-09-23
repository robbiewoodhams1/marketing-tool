"""Video metadata retrieval for discovered videos.

Search results (`YouTubeSearchResult`) only identify a video; this layer takes
video ids and returns detailed `YouTubeVideoMetadata`. The wire format lives in
`youtube.py`; this module adds validation, de-duplication, batching and
ordering.
"""

from __future__ import annotations

from collections.abc import Iterable

from research.logging import get_logger
from research.youtube import (
    MAX_VIDEO_IDS_PER_REQUEST,
    YouTubeClient,
    YouTubeVideoMetadata,
)

__all__ = ["YouTubeMetadata", "YouTubeVideoMetadata"]

log = get_logger("research.metadata")


class YouTubeMetadata:
    def __init__(self, client: YouTubeClient):
        self._client = client

    def get_metadata(self, video_ids: Iterable[str]) -> list[YouTubeVideoMetadata]:
        """Return metadata for the given video ids, in first-seen input order.

        Ids are stripped and de-duplicated; blank ids raise ValueError before
        any request. Ids are sent in batches of up to 50. Videos YouTube does
        not return (deleted, private, unknown) are omitted, never invented:
        find them with `{i.strip() for i in ids} - {v.video_id for v in result}`.
        `YouTubeError`s propagate unchanged.
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

        log.info("Metadata retrieval started: %d ids", len(ordered))
        by_id: dict[str, YouTubeVideoMetadata] = {}
        for start in range(0, len(ordered), MAX_VIDEO_IDS_PER_REQUEST):
            batch = ordered[start : start + MAX_VIDEO_IDS_PER_REQUEST]
            for video in self._client.get_videos(batch):
                by_id.setdefault(video.video_id, video)

        videos = [by_id[i] for i in ordered if i in by_id]
        missing = len(ordered) - len(videos)
        log.info(
            "Metadata retrieval completed: %d of %d videos returned",
            len(videos),
            len(ordered),
        )
        if missing:
            log.warning("%d requested videos were unavailable", missing)
        return videos
