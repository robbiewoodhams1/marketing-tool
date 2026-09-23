# Research service

Python service that will run the marketing tool's research engine: discovering
content for a research job, collecting transcripts and comments, and producing
insights and opportunities in Supabase (`research_jobs`, `content`, `comments`,
`insights`, `opportunities`).

**Status: foundation plus YouTube client (Chapter 1.3.1–1.3.2).** The service
starts, configures logging, and reports that it is running. A reusable YouTube
Data API v3 client exists (`research.youtube`) but nothing calls it yet, and
nothing is written to the database. The execution model (CLI, worker, or API) is not
decided yet.

## Requirements

Python 3.11 or newer (developed on 3.14). No runtime dependencies yet; `pytest`
is the only dev dependency.

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
| `SUPABASE_URL` | Supabase project URL | Not yet |
| `SUPABASE_KEY` | Supabase key | Not yet |
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

## Logging

Use `research.logging.get_logger("search")` (etc.) so later stages log under
the `research.*` namespace.

## Planned later

Relevance filtering, video metadata, transcript and comment retrieval, LLM
analysis, persisting results to Supabase, and job lifecycle handling.
