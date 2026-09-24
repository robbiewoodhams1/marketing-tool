# Research service

Python service that will run the marketing tool's research engine: discovering
content for a research job, collecting transcripts and comments, and producing
insights and opportunities in Supabase (`research_jobs`, `content`, `comments`,
`insights`, `opportunities`).

**Status: Chapters 1.3.1–1.3.8.** YouTube search, metadata, transcripts and
comments, Supabase persistence, and a terminal-triggered job runner
(`python -m research.runner --job-id ...`, see "Running a research job") are
implemented. There is no worker, scheduler or API, and no analysis yet.

## Requirements

Python 3.11 or newer (developed on 3.14). Runtime dependencies:
`youtube-transcript-api` (transcripts) and `postgrest` (Supabase writes);
`pytest` is the dev dependency.

## Install

```sh
cd services/research
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```sh
python -m research.main
# or, after install:
research
```

## Test

```sh
pytest
```

## Environment variables

None are required to start. Read via `research.config.Settings`; never commit
real values.

| Variable | Purpose | Required now |
|---|---|---|
| `RESEARCH_LOG_LEVEL` | Log level (default `INFO`) | No |
| `SUPABASE_URL` | Supabase project URL | Only when saving to Supabase |
| `SUPABASE_KEY` | Supabase key (unused) | No |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side service-role secret | Only when saving to Supabase |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key | Only when calling YouTube |
| `LLM_API_KEY` | LLM provider credentials | Not yet |

## YouTube API

`research.youtube.YouTubeClient` is the data-access layer for the YouTube Data
API v3 (`search.list`). It uses only the standard library and returns
`YouTubeSearchResult` dataclasses (`video_id`, `url`, `title`, `channel_id`,
`channel_title`, `published_at`), never raw API responses. Video statistics,
transcripts and comments are not part of it.

To get a key: in Google Cloud Console create a project, enable **YouTube Data
API v3**, then create an API key under Credentials. Set it as `YOUTUBE_API_KEY`
in your environment (never commit it). The key is only needed when a YouTube
call is made, not to import the package. Note each search costs 100 quota
units (default daily quota is 10,000).

```python
from datetime import datetime, timezone
from research.config import Settings
from research.youtube import YouTubeClient

client = YouTubeClient.from_settings(Settings.from_env())
for v in client.search_videos(
    "email marketing mistakes",
    max_results=10,  # 1-50
    published_after=datetime(2024, 1, 1, tzinfo=timezone.utc),
):
    print(v.video_id, v.title, v.url)
```

Failures raise subclasses of `YouTubeError`: `YouTubeConfigError` (no key),
`YouTubeAuthError` (bad/forbidden key), `YouTubeQuotaError` (quota or rate
limit), `YouTubeAPIError` (other HTTP errors, with `.status`),
`YouTubeNetworkError` and `YouTubeResponseError` (malformed response). There is
no retry logic yet. Tests use a fake transport and never hit the network.

## Research search

`research.search.YouTubeSearch` sits on top of the client. The split is:

- `youtube.py` (`YouTubeClient`): how to call YouTube: HTTP, errors, response
  parsing.
- `search.py` (`YouTubeSearch`): how the research system searches: validates
  the query (non-empty, at most 500 characters) and candidate limit (1-50,
  since pagination is not implemented), calls the client, removes duplicate
  video IDs (first seen wins) and keeps YouTube's ordering. It returns the same
  `YouTubeSearchResult` objects and lets `YouTubeError`s propagate. There is no
  relevance scoring or query rewriting.

```python
from research.search import YouTubeSearch

search = YouTubeSearch(YouTubeClient.from_settings(Settings.from_env()))
candidates = search.search("email marketing mistakes", max_candidates=20)
```

## Video metadata

`research.metadata.YouTubeMetadata` turns video ids into detailed
`YouTubeVideoMetadata` (`video_id`, `url`, `title`, `description`,
`channel_title`, `published_at`, `duration` as a `timedelta`, `view_count`,
`like_count`, `comment_count`). It uses `videos.list` through
`YouTubeClient.get_videos`, so the API wire format stays in `youtube.py`.

```python
from research.metadata import YouTubeMetadata

videos = YouTubeMetadata(client).get_metadata([c.video_id for c in candidates])
missing = {c.video_id for c in candidates} - {v.video_id for v in videos}
```

- Ids are stripped and de-duplicated, and blank ids raise `ValueError`.
  Empty input returns `[]` with no request.
- Ids are sent in batches of up to 50 per API call; results follow input order.
- Deleted/private/unknown videos are omitted rather than invented; use the set
  difference above to see which ids were not returned.
- Counts YouTube omits (hidden likes, disabled comments) are `None`, never `0`.

Search and metadata are separate stages because they are separate API calls
with different costs: `search.list` (100 quota units) only identifies
candidates, and `videos.list` (1 unit per call) fetches details, so cheap
filtering can happen between them. `YouTubeSearchResult` and
`YouTubeVideoMetadata` are therefore kept as distinct models.

## Transcripts

`research.transcripts.YouTubeTranscripts` returns raw caption text for video
ids as `YouTubeTranscript` (`video_id`, `text`, `language`, `is_generated`).
It is independent of search and metadata: it only needs video ids.

**Source: not the official YouTube Data API.** The Data API only lets a
video's owner download captions (OAuth), so it cannot supply transcript text
for arbitrary public videos. Transcripts come from the third-party
[`youtube-transcript-api`](https://pypi.org/project/youtube-transcript-api/)
package (`>=1.2,<2`), which reads YouTube's public caption endpoints. It needs
no API key and uses no Data API quota. It is wrapped by
`research.transcript_provider` behind the small `TranscriptProvider`
protocol, so it can be swapped without touching the rest of the system.

```python
from research.transcripts import YouTubeTranscripts

transcripts = YouTubeTranscripts()  # language="en", allow_fallback=False
results = transcripts.get_transcripts([v.video_id for v in videos])
missing = {v.video_id for v in videos} - {t.video_id for t in results}
one = transcripts.get_transcript("VIDEO_ID")  # raises if unavailable
```

- Ids are stripped and de-duplicated; blank ids raise `ValueError`; empty
  input returns `[]`. Results follow first-seen input order.
- Segments are joined with single spaces (line breaks and repeated whitespace
  collapse to one space). Wording is otherwise untouched.
- Videos with no transcript are omitted from `get_transcripts` and logged at
  WARNING; `get_transcript` raises the specific reason instead:
  `TranscriptsDisabledError`, `VideoUnavailableError` (missing, private,
  age-restricted, unplayable) or `TranscriptLanguageUnavailableError`, all
  subclasses of `TranscriptUnavailableError`. An empty transcript counts as
  unavailable.
- `TranscriptProviderError` (blocked, unparsable, malformed data) and
  `TranscriptNetworkError` are unexpected and propagate, aborting the batch.
  Both extend `TranscriptError`, as does `TranscriptConfigError` (blank
  language).

**Language.** `language="en"` (default) accepts `en` and regional variants
such as `en-GB`; a regional request like `en-GB` matches only that code.
Manual captions are preferred over auto-generated ones. If the language is
unavailable: by default the video is treated as unavailable
(`TranscriptLanguageUnavailableError`); with `allow_fallback=True` any other
available transcript is used (manual first) and `.language` says which. Any
language can be requested, e.g. `YouTubeTranscripts(language="es")`.

**Limitations.** This is unofficial scraping of public endpoints and can break
when YouTube changes. YouTube may block requests from cloud/datacentre IPs
(`TranscriptProviderError`); the library supports proxies but they are not
configured here. There are no retries, request timeout control or rate
limiting yet, and videos are fetched sequentially. Auto-generated captions
(`is_generated=True`) contain recognition errors and no punctuation guarantees.
Tests use fake providers and never hit the network.

## Comments

`research.comments.YouTubeComments` returns raw public **top-level** comments as
`YouTubeComment` (`video_id`, `comment_id`, `text`, `published_at`,
`like_count`, `updated_at`, `type`). These map onto the raw columns of
`public.comments` for chapter 1.3.7; nothing is written to the database here,
and no AI analysis (topic, pain point, desire, objection, sentiment) happens.

**Source: the official YouTube Data API v3**, `commentThreads.list`
(`part=snippet`, `textFormat=plainText`), through the existing `YouTubeClient`
(`get_comment_threads` fetches one page), so it uses the same key, transport
and error hierarchy. It needs `YOUTUBE_API_KEY`.

```python
from research.comments import YouTubeComments

comments = YouTubeComments(client).get_comments(
    [v.video_id for v in videos],
    max_comments_per_video=250,  # default 100, allowed 1-1000
    order="relevance",           # or "time"
)
```

- Returns one flat list: videos in first-seen input order, each video's
  comments in the order YouTube returned them. Nothing is re-sorted locally.
  `comment.video_id` ties each comment to its video.
- Ids are stripped and de-duplicated; blank ids and an out-of-range maximum
  raise `ValueError` before any request. Empty input returns `[]`.
- **Pagination:** pages of up to 100 follow `nextPageToken` until the cap is
  reached or YouTube has no more. The last page asks only for what is still
  needed (250 -> 100, 100, 50), and no request is made after the cap is hit.
- **Quota:** each page costs 1 unit (10,000/day by default), so the per-video
  cap bounds the cost: at most `ceil(cap / 100)` units per video. The
  1000-comment ceiling exists to prevent accidental unbounded pagination.
- **Unavailable comments:** comments disabled, video not found, or no comments
  gives no comments for that video (logged at WARNING) and the batch carries
  on. Auth, quota, network, other API errors and malformed responses raise the
  usual `YouTubeError` subclasses and abort the batch. (The client raises
  `YouTubeCommentsUnavailableError` for disabled/not-found.)
- **Text:** the plain-text form of the comment, with HTML entities decoded.
  Wording, spelling, slang, whitespace and profanity are kept as written.
- Dates are timezone-aware UTC. `like_count` and `updated_at` are `None` if
  YouTube omits them.
- **Privacy:** the author's name is not retained; only the public comment id
  is kept.
- **Limitations:** top-level comments only. Replies are not requested or
  returned (`type` is always `"top_level"`; replies may be added later as
  another `type`). No retries. Held-for-review or restricted comments are not
  visible to the API.

## Persistence (Supabase)

`research.persistence.ResearchRepository` saves acquired data into the existing
`research_jobs`, `content` and `comments` tables. The schema is not touched, and
the acquisition modules (`youtube`, `search`, `metadata`, `transcripts`,
`comments`) know nothing about Supabase. There is no orchestrator yet:
callers pass what they gathered.

```python
from research.persistence import ResearchRepository, ResearchVideo

repo = ResearchRepository.from_settings(Settings.from_env())
saved = repo.save_result(
    "sole trader bookkeeping", "UK sole traders", "Discover content opportunities",
    [ResearchVideo(metadata, transcript_or_None, comments) for ...],
)
saved.job_id, saved.summary  # rows inserted / skipped
```

Lower level: `create_job()`, `save_videos(job_id, videos)`, `complete_job()`,
`fail_job()`.

**Client.** `postgrest` (Supabase's own PostgREST client, `>=2,<3`; brings
`httpx` and `pydantic`). The full `supabase` package (auth, realtime, storage)
is not needed. Requests are batched: up to 500 rows per insert and 100 ids per
lookup; lookups page past PostgREST's 1000-row response limit.

**Environment variables** (read by `Settings`):

| Variable | Purpose |
|---|---|
| `SUPABASE_URL` | Project URL, e.g. `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | **Secret** service-role key |

`content`, `comments`, `insights` and `opportunities` have RLS enabled with no
policies, so only the service-role key (which bypasses RLS) can write to them;
the dashboard's publishable key cannot. The Python service is trusted backend
code. Keep the key in the environment or an untracked `.env`; never commit it,
put it in a `NEXT_PUBLIC_*` variable, or pass it to the browser. It is scrubbed
from database error messages and never logged. `SUPABASE_KEY` is unused.

**Mapping.**

- `research_jobs`: `query`, `audience`, `objective`, `status`, `started_at`
  (set when created), `completed_at`.
- `content` (one row per video, `platform="youtube"`): `external_id` = video
  id, `url`, `title`, `creator` = channel title, `description`, `published_at`,
  `duration_seconds`, `views`, `likes`, `comments_count`, `transcript` (text,
  or NULL if unavailable, never an empty string), `updated_at`. A duration over
  32,767 s (the column is a `smallint`) is stored as NULL with a warning.
- `comments`: `content_id`, `external_id` = comment id, `text`, `likes`, `type`
  (`top_level`).
- Never written, so NULL: `shares`, `saves`, and all analysis columns
  (`topic`, `audience`, `pain_point`, `hook`, `hook_type`, `format`, `emotion`,
  `cta`, `analysis_json` on content; `topic`, `pain_point`, `desire`,
  `objection`, `emotion` on comments). Timestamps are stored as UTC.

**Job lifecycle.** `running` (on create) -> `completed` (sets `completed_at`,
only after every write succeeded) or `failed` (`completed_at` stays NULL; the
schema has no error column, so the reason is in the logs, not the database). An
empty result is valid: a completed job with no content. `save_result` validates
inputs before writing anything.

**Idempotency.** The schema has no unique constraints on
`(research_job_id, external_id)` or `(content_id, external_id)`, so upserts are
not possible and duplicates are prevented by looking first:

- A video already stored for the same job is not inserted again or updated
  (first save wins); the same video in a different job is a separate row.
- A comment already stored for that content is skipped; repeats within the
  input are skipped too. Missing comments for already-stored videos are still
  added, so calling `save_videos` again on the same job id finishes a
  partly-saved job (then call `complete_job`).
- Limit: look-then-insert is not atomic, so two concurrent writers saving the
  same job could still create duplicates. Adding unique indexes would fix that
  (a schema change, out of scope here).

**Failure behaviour and transaction limitation.** PostgREST offers no
transaction across requests, and this layer does not open direct Postgres
connections, so a save is a sequence of requests, not one atomic unit. If one
fails, the job is marked `failed` (best effort) and the error is re-raised as
`PersistenceError`; rows already written stay in place. Nothing is rolled back
and a failed job is never reported as completed.

**Real Supabase smoke test** (writes real rows, so it is separate from the unit
tests and needs `--confirm-write`; it saves 1 job, 1 video, at most 5 comments,
deletes nothing, and prints the job id to inspect):

```sh
export YOUTUBE_API_KEY=... SUPABASE_URL=https://<ref>.supabase.co \
       SUPABASE_SERVICE_ROLE_KEY=...
python scripts/smoke_persistence.py --video-id VIDEO_ID --confirm-write
```

Unit tests use an in-memory fake database and an `httpx` mock transport; they
never contact Supabase.

## Running a research job

`python -m research.runner --job-id JOB_ID` takes one **existing** job that the
dashboard created (`status = queued`) and runs the whole pipeline for it from
the terminal. It never creates a job. This is a manual, one-shot command: no
polling, scheduling, worker or server.

```sh
cd services/research
source .venv/bin/activate
export YOUTUBE_API_KEY=... SUPABASE_URL=https://<ref>.supabase.co \
       SUPABASE_SERVICE_ROLE_KEY=...        # secrets: never commit
python -m research.runner --job-id d8aee101-d48c-4be5-9247-57b33b56fe65
```

Environment variables are read from the process environment only (there is no
`.env` loader), and the service-role key is a server-side secret.

Flow: `queued -> running` (sets `started_at`; `query`, `audience`, `objective`
and `created_at` are untouched) -> `YouTubeSearch` on the job's query (10
candidates) -> `YouTubeMetadata` -> `YouTubeTranscripts` -> `YouTubeComments`
(default 100 per video) -> `ResearchRepository.save_videos` against the same
job -> `completed` (sets `completed_at`). Progress is printed as it goes. The
orchestration lives in `research.pipeline.run_research_job` (services are
injected, so it is tested without YouTube or Supabase); `research.runner` only
parses the arguments and builds the real services.

- **Not runnable:** a job that does not exist, or whose status is `running`,
  `completed` or `failed`, is refused without changing anything. There is no
  retry; a failed job stays failed.
- **Unavailable data is not a failure:** videos without a transcript get
  `transcript = NULL`; videos with comments disabled are saved without
  comments; videos YouTube will not return metadata for are skipped. Empty
  search results complete the job with no content.
- **Fatal errors** (YouTube auth/quota/network, transcript provider or network
  errors, database errors, Ctrl-C) mark the job `failed` with `completed_at`
  left NULL, print the error and exit non-zero. Anything already saved stays,
  because persistence is not transactional. The reason is in the terminal and
  logs only; the schema has no error column.
- **Exit codes:** 0 completed; 1 job missing or not runnable; 2 bad arguments
  or missing configuration (nothing touched); 3 the run failed.
- **Concurrency:** `queued -> running` is one conditional update, so if two
  terminals run the same job only one proceeds and the other is refused. There
  is no other locking, and a process killed hard (`kill -9`, power loss)
  leaves its job `running` with no automatic recovery.
- **Quota:** one search (100 units) plus one metadata call and up to one
  comments page per video (about 1 unit each), so roughly 120 units for 10
  videos. Transcripts do not use the YouTube Data API quota.

## Logging

Use `research.logging.get_logger("search")` (etc.) so later stages log under
the `research.*` namespace.

## Planned later

Relevance filtering, retries, scheduling/polling, and LLM analysis.
