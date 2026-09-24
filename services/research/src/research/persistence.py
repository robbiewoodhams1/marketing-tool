"""Persist raw research results to Supabase (chapter 1.3.7).

Takes the objects the acquisition layer already produces (video metadata,
transcripts, comments) and stores them in the existing `research_jobs`,
`content` and `comments` tables. Nothing here calls YouTube, and no analysis
columns are ever written: they stay NULL for chapter 1.4.

Writes go through the small `Database` protocol so tests never need Supabase;
`SupabaseDatabase` implements it with the official `postgrest` client using the
service-role key (a server-side secret that bypasses RLS).

Limitation: PostgREST has no multi-statement transactions, so a save is a
sequence of batched requests. A failure part-way leaves earlier rows in place
and marks the job "failed"; see `ResearchRepository.save_result`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from research.config import Settings
from research.logging import get_logger
from research.transcripts import YouTubeTranscript
from research.youtube import YouTubeComment, YouTubeVideoMetadata

log = get_logger("research.persistence")

PLATFORM_YOUTUBE = "youtube"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

INSERT_BATCH_SIZE = 500  # rows per insert request
LOOKUP_BATCH_SIZE = 100  # ids per `in (...)` filter, keeps URLs short
SELECT_PAGE_SIZE = 1000  # PostgREST's default max rows per response
MAX_DURATION_SECONDS = 32767  # content.duration_seconds is a smallint


class PersistenceError(Exception):
    """A database operation failed."""


class PersistenceConfigError(PersistenceError):
    """Supabase URL or service-role key is not configured."""


class Database(Protocol):
    """The three operations the repository needs; rows are plain dicts."""

    def insert(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert rows in one request and return them as stored (with ids)."""
        ...

    def select(
        self,
        table: str,
        columns: str,
        *,
        eq: dict[str, Any] | None = None,
        in_: tuple[str, list[str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Return ALL matching rows (implementations must page past row limits)."""
        ...

    def update(
        self, table: str, values: dict[str, Any], *, eq: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Update rows matching ALL `eq` filters; return the rows changed."""
        ...


class SupabaseDatabase:
    """`Database` over Supabase's PostgREST API. Errors become PersistenceError."""

    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        timeout: float = 30.0,
        http_client: Any = None,  # an httpx.Client; injectable for tests
    ):
        import httpx
        from postgrest import SyncPostgrestClient

        self._secret = service_role_key
        self._client = SyncPostgrestClient(
            f"{url.rstrip('/')}/rest/v1",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
            },
            http_client=http_client or httpx.Client(timeout=timeout),
        )

    def _fail(self, action: str, table: str, exc: Exception) -> PersistenceError:
        from postgrest import APIError

        detail = type(exc).__name__
        if isinstance(exc, APIError):
            detail = f"{detail} code={exc.code} message={exc.message}"
        # Never let the credential reach logs or exception text.
        detail = detail.replace(self._secret, "***")
        return PersistenceError(f"Supabase {action} on {table} failed: {detail}")

    def insert(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            return list(self._client.from_(table).insert(rows).execute().data)
        except Exception as exc:
            raise self._fail("insert", table, exc) from None

    def select(
        self,
        table: str,
        columns: str,
        *,
        eq: dict[str, Any] | None = None,
        in_: tuple[str, list[str]] | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            start = 0
            while True:
                query = self._client.from_(table).select(columns)
                for column, value in (eq or {}).items():
                    query = query.eq(column, value)
                if in_:
                    query = query.in_(in_[0], in_[1])
                page = list(
                    query.order("id").range(start, start + SELECT_PAGE_SIZE - 1).execute().data
                )
                rows.extend(page)
                if len(page) < SELECT_PAGE_SIZE:
                    return rows
                start += SELECT_PAGE_SIZE
        except Exception as exc:
            raise self._fail("select", table, exc) from None

    def update(
        self, table: str, values: dict[str, Any], *, eq: dict[str, Any]
    ) -> list[dict[str, Any]]:
        try:
            query = self._client.from_(table).update(values)
            for column, value in eq.items():
                query = query.eq(column, value)
            return list(query.execute().data)
        except Exception as exc:
            raise self._fail("update", table, exc) from None


@dataclass(frozen=True)
class ResearchJob:
    """An existing research_jobs row, as the runner needs it."""

    id: str
    query: str | None
    audience: str | None
    objective: str | None
    status: str | None


@dataclass(frozen=True)
class ResearchVideo:
    """Everything acquired for one video. `transcript` is None if unavailable."""

    metadata: YouTubeVideoMetadata
    transcript: YouTubeTranscript | None = None
    comments: Sequence[YouTubeComment] = field(default_factory=tuple)


@dataclass(frozen=True)
class SaveSummary:
    content_inserted: int = 0
    content_skipped: int = 0  # already stored for this job
    comments_inserted: int = 0
    comments_skipped: int = 0  # already stored, or repeated in the input


@dataclass(frozen=True)
class SavedResearch:
    job_id: str
    summary: SaveSummary


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: datetime | None, what: str) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError(f"{what} must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _content_row(job_id: str, video: ResearchVideo) -> dict[str, Any]:
    meta = video.metadata
    duration = None
    if meta.duration is not None:
        seconds = int(meta.duration.total_seconds())
        if seconds <= MAX_DURATION_SECONDS:
            duration = seconds
        else:
            log.warning(
                "Video %s duration %ds exceeds the smallint column; storing NULL",
                meta.video_id,
                seconds,
            )
    transcript = video.transcript.text if video.transcript else None
    return {
        "research_job_id": job_id,
        "platform": PLATFORM_YOUTUBE,
        "external_id": meta.video_id,
        "url": meta.url,
        "title": meta.title,
        "creator": meta.channel_title,
        "description": meta.description,
        "published_at": _timestamp(meta.published_at, "published_at"),
        "duration_seconds": duration,
        "views": meta.view_count,
        "likes": meta.like_count,
        "comments_count": meta.comment_count,
        "transcript": transcript if transcript and transcript.strip() else None,
        "updated_at": _now(),
        # shares, saves and every analysis column are deliberately omitted (NULL).
    }


def _comment_row(content_id: str, comment: YouTubeComment) -> dict[str, Any]:
    return {
        "content_id": content_id,
        "external_id": comment.comment_id,
        "text": comment.text,
        "likes": comment.like_count,
        "type": comment.type,
        # topic, pain_point, desire, objection, emotion: analysis stage, NULL.
    }


def _validate(videos: Sequence[ResearchVideo]) -> list[ResearchVideo]:
    """Check consistency and drop repeated videos (first seen wins)."""
    unique: dict[str, ResearchVideo] = {}
    for video in videos:
        video_id = video.metadata.video_id
        if video.transcript and video.transcript.video_id != video_id:
            raise ValueError(f"transcript video_id does not match metadata for {video_id}")
        for comment in video.comments:
            if comment.video_id != video_id:
                raise ValueError(f"comment {comment.comment_id} does not belong to {video_id}")
        _content_row("", video)  # surfaces naive datetimes before anything is written
        unique.setdefault(video_id, video)
    return list(unique.values())


class ResearchRepository:
    def __init__(self, db: Database):
        self._db = db

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> ResearchRepository:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise PersistenceConfigError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to save results"
            )
        return cls(
            SupabaseDatabase(
                settings.supabase_url, settings.supabase_service_role_key, **kwargs
            )
        )

    def create_job(
        self, query: str, audience: str | None = None, objective: str | None = None
    ) -> str:
        """Insert a research job with status "running" and return its id."""
        if not query.strip():
            raise ValueError("query must not be empty")
        [row] = self._db.insert(
            "research_jobs",
            [
                {
                    "query": query,
                    "audience": audience,
                    "objective": objective,
                    "status": STATUS_RUNNING,
                    "started_at": _now(),
                }
            ],
        )
        log.info("Research job %s created", row["id"])
        return str(row["id"])

    def get_job(self, job_id: str) -> ResearchJob | None:
        """Load a job, or None if no such row exists."""
        rows = self._db.select(
            "research_jobs", "id,query,audience,objective,status", eq={"id": job_id}
        )
        if not rows:
            return None
        row = rows[0]
        return ResearchJob(
            id=str(row["id"]),
            query=row.get("query"),
            audience=row.get("audience"),
            objective=row.get("objective"),
            status=row.get("status"),
        )

    def start_job(self, job_id: str) -> bool:
        """Atomically move a job from "queued" to "running" and set started_at.

        The update is conditional on the status still being "queued", so if two
        processes race for the same job only one gets True. Returns False if
        the job was not queued (or does not exist). query, audience, objective
        and created_at are not touched.
        """
        changed = self._db.update(
            "research_jobs",
            {"status": STATUS_RUNNING, "started_at": _now()},
            eq={"id": job_id, "status": STATUS_QUEUED},
        )
        if changed:
            log.info("Research job %s started", job_id)
        return bool(changed)

    def complete_job(self, job_id: str) -> None:
        self._db.update(
            "research_jobs",
            {"status": STATUS_COMPLETED, "completed_at": _now()},
            eq={"id": job_id},
        )
        log.info("Research job %s completed", job_id)

    def fail_job(self, job_id: str) -> None:
        """Mark failed. completed_at stays NULL: it only ever means success.

        The schema has no error column, so the reason is logged, not stored.
        """
        self._db.update("research_jobs", {"status": STATUS_FAILED}, eq={"id": job_id})
        log.error("Research job %s marked failed", job_id)

    def save_videos(self, job_id: str, videos: Sequence[ResearchVideo]) -> SaveSummary:
        """Save content and comments for a job. Safe to repeat (see below).

        There are no unique constraints in the schema, so duplicates are
        prevented by checking first: a video already stored for this job (same
        platform + external_id) is not re-inserted or updated (first save
        wins), and a comment already stored for that content (same external_id)
        is skipped, as are repeats within the input. Comments for
        already-stored videos are still topped up, so re-running after a partial
        failure completes the job's data. Checking is not atomic: two writers
        saving the same job concurrently could still duplicate rows.

        Requests are batched: one lookup and one insert per chunk of videos,
        and inserts of up to 500 comments each.
        """
        videos = _validate(videos)
        if not videos:
            return SaveSummary()

        # content: find what exists, insert the rest
        content_ids: dict[str, str] = {}
        for chunk in _chunks([v.metadata.video_id for v in videos], LOOKUP_BATCH_SIZE):
            rows = self._db.select(
                "content",
                "id,external_id",
                eq={"research_job_id": job_id, "platform": PLATFORM_YOUTUBE},
                in_=("external_id", list(chunk)),
            )
            content_ids.update({r["external_id"]: r["id"] for r in rows})
        existing = set(content_ids)

        new_videos = [v for v in videos if v.metadata.video_id not in existing]
        for chunk in _chunks(new_videos, INSERT_BATCH_SIZE):
            stored = self._db.insert("content", [_content_row(job_id, v) for v in chunk])
            content_ids.update({r["external_id"]: r["id"] for r in stored})
        missing = [v.metadata.video_id for v in videos if v.metadata.video_id not in content_ids]
        if missing:
            raise PersistenceError(f"Database did not return content rows for {missing}")

        # comments: only already-stored content can have stored comments
        known: set[tuple[str, str]] = set()
        for chunk in _chunks([content_ids[i] for i in existing], LOOKUP_BATCH_SIZE):
            rows = self._db.select("comments", "id,content_id,external_id", in_=("content_id", list(chunk)))
            known.update((r["content_id"], r["external_id"]) for r in rows)

        to_insert: list[dict[str, Any]] = []
        skipped = 0
        for video in videos:
            content_id = content_ids[video.metadata.video_id]
            for comment in video.comments:
                key = (content_id, comment.comment_id)
                if key in known:
                    skipped += 1
                    continue
                known.add(key)
                to_insert.append(_comment_row(content_id, comment))
        for chunk in _chunks(to_insert, INSERT_BATCH_SIZE):
            self._db.insert("comments", list(chunk))

        summary = SaveSummary(
            content_inserted=len(new_videos),
            content_skipped=len(existing),
            comments_inserted=len(to_insert),
            comments_skipped=skipped,
        )
        log.info("Saved research data for job %s: %s", job_id, summary)
        return summary

    def save_result(
        self,
        query: str,
        audience: str | None,
        objective: str | None,
        videos: Sequence[ResearchVideo],
    ) -> SavedResearch:
        """Full lifecycle: create job, save everything, mark completed.

        Inputs are validated before anything is written. If a database call
        fails after the job exists, the job is marked "failed" (best effort)
        and the original error is re-raised; rows already written are kept
        because there is no cross-request transaction. A job is only
        "completed" after every write succeeded. An empty `videos` list is a
        legitimate result: the job is created and completed with no content.
        """
        videos = _validate(videos)
        job_id = self.create_job(query, audience, objective)
        try:
            summary = self.save_videos(job_id, videos)
            self.complete_job(job_id)
        except Exception as exc:
            log.error(
                "Saving research job %s failed: %s: %s", job_id, type(exc).__name__, exc
            )
            try:
                self.fail_job(job_id)
            except PersistenceError as fail_exc:
                log.error("Could not mark job %s failed: %s", job_id, fail_exc)
            raise
        return SavedResearch(job_id, summary)
