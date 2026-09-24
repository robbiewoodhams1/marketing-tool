import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote

import httpx
import pytest

from research.config import Settings
from research.persistence import (
    INSERT_BATCH_SIZE,
    PersistenceConfigError,
    PersistenceError,
    ResearchRepository,
    ResearchVideo,
    SaveSummary,
    SupabaseDatabase,
)
from research.transcripts import YouTubeTranscript
from research.youtube import YouTubeComment, YouTubeVideoMetadata

SECRET = "service-role-secret-not-real"
PUBLISHED = datetime(2024, 3, 5, 14, 30, tzinfo=timezone.utc)


class FakeDatabase:
    """In-memory tables with generated ids, call log and failure injection."""

    def __init__(self, fail_on=None):
        self.tables = {"research_jobs": [], "content": [], "comments": []}
        self.calls = []
        self.fail_on = fail_on  # (operation, table)
        self._n = 0

    def _maybe_fail(self, op, table):
        if self.fail_on == (op, table):
            raise PersistenceError(f"boom {op} {table}")

    def insert(self, table, rows):
        self.calls.append(("insert", table, len(rows)))
        self._maybe_fail("insert", table)
        stored = []
        for row in rows:
            self._n += 1
            stored.append({"id": f"id-{self._n}", **row})
        self.tables[table].extend(stored)
        return [dict(r) for r in stored]

    def select(self, table, columns, *, eq=None, in_=None):
        self.calls.append(("select", table, None))
        self._maybe_fail("select", table)
        rows = self.tables[table]
        for k, v in (eq or {}).items():
            rows = [r for r in rows if r.get(k) == v]
        if in_:
            rows = [r for r in rows if r.get(in_[0]) in in_[1]]
        return [dict(r) for r in rows]

    def update(self, table, values, *, eq):
        self.calls.append(("update", table, None))
        self._maybe_fail("update", table)
        changed = []
        for row in self.tables[table]:
            if all(row.get(k) == v for k, v in eq.items()):
                row.update(values)
                changed.append(dict(row))
        return changed


def meta(video_id="vid1", **kw):
    fields = dict(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title="A title",
        channel_title="A Channel",
        published_at=PUBLISHED,
        description="A description",
        duration=timedelta(minutes=5, seconds=30),
        view_count=1500,
        like_count=40,
        comment_count=7,
    )
    fields.update(kw)
    return YouTubeVideoMetadata(**fields)


def comment(video_id, n, **kw):
    fields = dict(
        video_id=video_id,
        comment_id=f"{video_id}-c{n}",
        text=f"comment {n}",
        published_at=PUBLISHED,
        like_count=n,
    )
    fields.update(kw)
    return YouTubeComment(**fields)


def video(video_id="vid1", comments=0, transcript="hello world", **kw):
    return ResearchVideo(
        meta(video_id, **kw),
        YouTubeTranscript(video_id, transcript, "en", False) if transcript else None,
        [comment(video_id, n) for n in range(1, comments + 1)],
    )


def repo(**kw):
    db = FakeDatabase(**kw)
    return ResearchRepository(db), db


ANALYSIS_CONTENT = ["topic", "audience", "pain_point", "hook", "hook_type", "format",
                    "emotion", "cta", "analysis_json", "shares", "saves"]
ANALYSIS_COMMENT = ["topic", "pain_point", "desire", "objection", "emotion"]


# --- job lifecycle -----------------------------------------------------------


def test_create_job_maps_fields_and_starts_running():
    r, db = repo()
    job_id = r.create_job("sole trader bookkeeping", "UK sole traders", "Find opportunities")
    [row] = db.tables["research_jobs"]
    assert row["id"] == job_id
    assert (row["query"], row["audience"], row["objective"]) == (
        "sole trader bookkeeping", "UK sole traders", "Find opportunities")
    assert row["status"] == "running"
    started = datetime.fromisoformat(row["started_at"])
    assert started.tzinfo is not None
    assert "completed_at" not in row


def test_blank_query_rejected():
    r, db = repo()
    with pytest.raises(ValueError):
        r.create_job("  ")
    assert db.calls == []


def test_complete_and_fail_job():
    r, db = repo()
    a, b = r.create_job("q"), r.create_job("q2")
    r.complete_job(a)
    r.fail_job(b)
    rows = {row["id"]: row for row in db.tables["research_jobs"]}
    assert rows[a]["status"] == "completed"
    assert datetime.fromisoformat(rows[a]["completed_at"]).tzinfo is not None
    assert rows[b]["status"] == "failed"
    assert "completed_at" not in rows[b]


# --- mapping -----------------------------------------------------------------


def test_content_mapping():
    r, db = repo()
    job_id = r.create_job("q")
    r.save_videos(job_id, [video()])
    [row] = db.tables["content"]
    assert row["research_job_id"] == job_id
    assert row["platform"] == "youtube"
    assert row["external_id"] == "vid1"
    assert row["url"] == "https://www.youtube.com/watch?v=vid1"
    assert row["title"] == "A title"
    assert row["creator"] == "A Channel"
    assert row["description"] == "A description"
    assert row["published_at"] == "2024-03-05T14:30:00+00:00"
    assert row["duration_seconds"] == 330
    assert (row["views"], row["likes"], row["comments_count"]) == (1500, 40, 7)
    assert row["transcript"] == "hello world"
    assert datetime.fromisoformat(row["updated_at"]).tzinfo is not None


def test_analysis_shares_and_saves_are_not_written():
    r, db = repo()
    r.save_result("q", None, None, [video(comments=1)])
    for column in ANALYSIS_CONTENT:
        assert column not in db.tables["content"][0]
    for column in ANALYSIS_COMMENT:
        assert column not in db.tables["comments"][0]


def test_unavailable_transcript_is_null_not_empty_string():
    r, db = repo()
    r.save_result("q", None, None, [video("a", transcript=None), video("b", transcript="   ")])
    assert [row["transcript"] for row in db.tables["content"]] == [None, None]


def test_optional_metadata_none_stays_none():
    r, db = repo()
    r.save_result("q", None, None, [video(description=None, duration=None, view_count=None,
                                          like_count=None, comment_count=None)])
    row = db.tables["content"][0]
    assert all(row[k] is None for k in
               ["description", "duration_seconds", "views", "likes", "comments_count"])


def test_duration_too_long_for_smallint_becomes_null(caplog):
    r, db = repo()
    with caplog.at_level(logging.WARNING, logger="research.persistence"):
        r.save_result("q", None, None, [video(duration=timedelta(hours=10))])
    assert db.tables["content"][0]["duration_seconds"] is None
    assert "smallint" in caplog.text


def test_non_utc_datetime_normalised_to_utc():
    r, db = repo()
    bst = timezone(timedelta(hours=1))
    r.save_result("q", None, None, [video(published_at=datetime(2024, 6, 1, 12, 0, tzinfo=bst))])
    assert db.tables["content"][0]["published_at"] == "2024-06-01T11:00:00+00:00"


def test_naive_datetime_rejected_before_anything_is_written():
    r, db = repo()
    with pytest.raises(ValueError, match="timezone-aware"):
        r.save_result("q", None, None, [video(published_at=datetime(2024, 1, 1))])
    assert db.calls == []


def test_comment_mapping_and_relationship():
    r, db = repo()
    r.save_result("q", None, None, [video("a", comments=2), video("b", comments=1)])
    content = {row["external_id"]: row["id"] for row in db.tables["content"]}
    rows = db.tables["comments"]
    assert [(c["content_id"], c["external_id"]) for c in rows] == [
        (content["a"], "a-c1"), (content["a"], "a-c2"), (content["b"], "b-c1")]
    assert rows[1] == {"id": rows[1]["id"], "content_id": content["a"],
                       "external_id": "a-c2", "text": "comment 2", "likes": 2,
                       "type": "top_level"}


def test_comment_with_unknown_likes_stores_null():
    r, db = repo()
    v = ResearchVideo(meta(), None, [comment("vid1", 1, like_count=None)])
    r.save_result("q", None, None, [v])
    assert db.tables["comments"][0]["likes"] is None


def test_mismatched_transcript_or_comment_rejected():
    r, db = repo()
    bad_t = ResearchVideo(meta("a"), YouTubeTranscript("b", "x", "en", False))
    bad_c = ResearchVideo(meta("a"), None, [comment("b", 1)])
    for bad in (bad_t, bad_c):
        with pytest.raises(ValueError):
            r.save_result("q", None, None, [bad])
    assert db.calls == []


# --- multiple videos / batching ------------------------------------------------


def test_multiple_videos_saved_in_order_and_batched():
    r, db = repo()
    result = r.save_result("q", None, None, [video("c", comments=2), video("a", comments=3),
                                             video("b", comments=1)])
    assert [row["external_id"] for row in db.tables["content"]] == ["c", "a", "b"]
    assert result.summary == SaveSummary(3, 0, 6, 0)
    inserts = [c for c in db.calls if c[0] == "insert"]
    assert inserts == [("insert", "research_jobs", 1), ("insert", "content", 3),
                       ("insert", "comments", 6)]


def test_large_comment_set_split_into_batches():
    r, db = repo()
    n = INSERT_BATCH_SIZE * 2 + 1
    r.save_result("q", None, None, [video(comments=n)])
    sizes = [c[2] for c in db.calls if c[:2] == ("insert", "comments")]
    assert sizes == [INSERT_BATCH_SIZE, INSERT_BATCH_SIZE, 1]
    assert len(db.tables["comments"]) == n


def test_empty_result_creates_and_completes_job_only():
    r, db = repo()
    result = r.save_result("q", "aud", "obj", [])
    assert result.summary == SaveSummary()
    assert db.tables["content"] == [] and db.tables["comments"] == []
    assert db.tables["research_jobs"][0]["status"] == "completed"


# --- idempotency ----------------------------------------------------------------


def test_duplicate_videos_in_input_saved_once():
    r, db = repo()
    result = r.save_result("q", None, None, [video("a", comments=1), video("a", comments=5)])
    assert len(db.tables["content"]) == 1
    assert len(db.tables["comments"]) == 1
    assert result.summary.content_inserted == 1


def test_duplicate_comments_in_input_saved_once():
    r, db = repo()
    v = ResearchVideo(meta(), None, [comment("vid1", 1), comment("vid1", 1), comment("vid1", 2)])
    result = r.save_result("q", None, None, [v])
    assert len(db.tables["comments"]) == 2
    assert result.summary.comments_skipped == 1


def test_resaving_same_job_creates_no_duplicates():
    r, db = repo()
    job_id = r.create_job("q")
    r.save_videos(job_id, [video("a", comments=2)])
    summary = r.save_videos(job_id, [video("a", comments=2)])
    assert len(db.tables["content"]) == 1 and len(db.tables["comments"]) == 2
    assert summary == SaveSummary(0, 1, 0, 2)


def test_resave_tops_up_missing_comments_after_partial_failure():
    r, db = repo()
    job_id = r.create_job("q")
    r.save_videos(job_id, [video("a", comments=1)])
    summary = r.save_videos(job_id, [video("a", comments=3)])
    assert len(db.tables["comments"]) == 3
    assert summary.comments_inserted == 2 and summary.content_skipped == 1


def test_existing_content_is_not_overwritten():
    r, db = repo()
    job_id = r.create_job("q")
    r.save_videos(job_id, [video("a", title="first")])
    r.save_videos(job_id, [video("a", title="second")])
    assert db.tables["content"][0]["title"] == "first"


def test_same_video_in_different_jobs_is_allowed():
    r, db = repo()
    r.save_result("q", None, None, [video("a", comments=1)])
    r.save_result("q", None, None, [video("a", comments=1)])
    assert len(db.tables["content"]) == 2 and len(db.tables["comments"]) == 2


# --- failures ------------------------------------------------------------------


def test_job_completed_only_after_success():
    r, db = repo()
    r.save_result("q", None, None, [video()])
    assert db.tables["research_jobs"][0]["status"] == "completed"


@pytest.mark.parametrize("op, table", [("insert", "content"), ("insert", "comments"),
                                       ("select", "content")])
def test_failure_marks_job_failed_and_reraises(op, table, caplog):
    r, db = repo(fail_on=(op, table))
    with caplog.at_level(logging.ERROR, logger="research.persistence"):
        with pytest.raises(PersistenceError, match="boom"):
            r.save_result("q", None, None, [video(comments=1)])
    job = db.tables["research_jobs"][0]
    assert job["status"] == "failed"
    assert "completed_at" not in job
    assert "boom" in caplog.text


def test_failure_to_create_job_writes_nothing():
    r, db = repo(fail_on=("insert", "research_jobs"))
    with pytest.raises(PersistenceError):
        r.save_result("q", None, None, [video()])
    assert db.tables["content"] == []


def test_failure_while_marking_failed_still_raises_original(caplog):
    class Db(FakeDatabase):
        def update(self, table, values, *, eq):
            raise PersistenceError("update also broke")

    db = Db(fail_on=("insert", "content"))
    with pytest.raises(PersistenceError, match="boom"):
        ResearchRepository(db).save_result("q", None, None, [video()])


def test_unexpected_exception_also_fails_job():
    class Db(FakeDatabase):
        def insert(self, table, rows):
            if table == "content":
                raise RuntimeError("weird")
            return super().insert(table, rows)

    db = Db()
    with pytest.raises(RuntimeError):
        ResearchRepository(db).save_result("q", None, None, [video()])
    assert db.tables["research_jobs"][0]["status"] == "failed"


def test_missing_rows_returned_from_insert_is_an_error():
    class Db(FakeDatabase):
        def insert(self, table, rows):
            return [] if table == "content" else super().insert(table, rows)

    with pytest.raises(PersistenceError):
        ResearchRepository(Db()).save_result("q", None, None, [video()])


# --- configuration and the Supabase adapter -----------------------------------------


@pytest.mark.parametrize("env", [{}, {"SUPABASE_URL": "https://x.supabase.co"},
                                 {"SUPABASE_SERVICE_ROLE_KEY": SECRET}])
def test_from_settings_requires_url_and_service_role_key(env):
    with pytest.raises(PersistenceConfigError) as info:
        ResearchRepository.from_settings(Settings.from_env(env))
    assert SECRET not in str(info.value)


def test_settings_read_service_role_key_separately():
    s = Settings.from_env({"SUPABASE_SERVICE_ROLE_KEY": SECRET, "SUPABASE_KEY": "anon"})
    assert s.supabase_service_role_key == SECRET and s.supabase_key == "anon"


class Recorder:
    def __init__(self, handler):
        self.requests = []
        self.handler = handler

    def __call__(self, request):
        self.requests.append(request)
        return self.handler(request)


def supabase_db(handler):
    rec = Recorder(handler)
    client = httpx.Client(transport=httpx.MockTransport(rec))
    return SupabaseDatabase("https://proj.supabase.co/", SECRET, http_client=client), rec


def test_adapter_insert_request_shape():
    db, rec = supabase_db(lambda req: httpx.Response(201, json=[{"id": "1", "query": "q"}]))
    assert db.insert("research_jobs", [{"query": "q"}]) == [{"id": "1", "query": "q"}]
    [req] = rec.requests
    assert req.method == "POST" and req.url.path == "/rest/v1/research_jobs"
    assert req.headers["apikey"] == SECRET
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(req.content) == [{"query": "q"}]


def test_adapter_select_filters_and_pages_past_row_limit():
    def handler(req):
        params = parse_qs(req.url.query.decode())
        assert params["limit"] == ["1000"] and params["order"] == ["id.asc"]
        start = int(params["offset"][0])
        n = 1000 if start == 0 else 5
        return httpx.Response(200, json=[{"id": str(start + i)} for i in range(n)])

    db, rec = supabase_db(handler)
    rows = db.select("content", "id", eq={"platform": "youtube"}, in_=("external_id", ["a", "b"]))
    assert len(rows) == 1005 and len(rec.requests) == 2
    q = parse_qs(rec.requests[0].url.query.decode())
    assert q["platform"] == ["eq.youtube"]
    assert unquote(q["external_id"][0]) == "in.(a,b)"


def test_adapter_update_filters_by_id():
    db, rec = supabase_db(lambda req: httpx.Response(200, json=[{"id": "abc"}]))
    changed = db.update("research_jobs", {"status": "failed"}, eq={"id": "abc", "status": "queued"})
    req = rec.requests[0]
    assert req.method == "PATCH"
    q = parse_qs(req.url.query.decode())
    assert q["id"] == ["eq.abc"] and q["status"] == ["eq.queued"]
    assert changed == [{"id": "abc"}]


def test_adapter_api_error_is_wrapped_without_secret():
    db, _ = supabase_db(lambda req: httpx.Response(
        401, json={"message": f"bad key {SECRET}", "code": "PGRST301",
                   "details": None, "hint": None}))
    with pytest.raises(PersistenceError) as info:
        db.insert("content", [{}])
    assert "PGRST301" in str(info.value)
    assert SECRET not in str(info.value)


def test_adapter_network_error_is_wrapped_without_secret():
    def handler(req):
        raise httpx.ConnectError(f"cannot connect with {SECRET}")

    db, _ = supabase_db(handler)
    with pytest.raises(PersistenceError) as info:
        db.select("content", "id")
    assert "ConnectError" in str(info.value) and SECRET not in str(info.value)
    assert info.value.__cause__ is None


# --- runner support: get_job / start_job -----------------------------------------------


def queued_job(r, db, **row):
    db.tables["research_jobs"].append(
        {"id": "job-1", "query": "q", "audience": "aud", "objective": "obj",
         "status": "queued", "created_at": "2026-01-01T00:00:00+00:00", **row})


def test_get_job_returns_existing_row_or_none():
    r, db = repo()
    queued_job(r, db)
    job = r.get_job("job-1")
    assert (job.id, job.query, job.audience, job.objective, job.status) == (
        "job-1", "q", "aud", "obj", "queued")
    assert r.get_job("nope") is None


def test_start_job_moves_queued_to_running_and_only_touches_status_and_started_at():
    r, db = repo()
    queued_job(r, db)
    assert r.start_job("job-1") is True
    row = db.tables["research_jobs"][0]
    assert row["status"] == "running"
    assert datetime.fromisoformat(row["started_at"]).tzinfo is not None
    assert (row["query"], row["audience"], row["objective"], row["created_at"]) == (
        "q", "aud", "obj", "2026-01-01T00:00:00+00:00")
    assert len(db.tables["research_jobs"]) == 1


@pytest.mark.parametrize("status", ["running", "completed", "failed", None])
def test_start_job_refuses_non_queued_and_changes_nothing(status):
    r, db = repo()
    queued_job(r, db, status=status)
    assert r.start_job("job-1") is False
    assert db.tables["research_jobs"][0]["status"] == status
    assert "started_at" not in db.tables["research_jobs"][0]


def test_start_job_second_caller_loses_the_race():
    r, db = repo()
    queued_job(r, db)
    assert [r.start_job("job-1"), r.start_job("job-1")] == [True, False]


def test_start_job_unknown_id_is_false():
    r, _ = repo()
    assert r.start_job("nope") is False
