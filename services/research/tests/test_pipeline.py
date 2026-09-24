import json
from datetime import datetime

import pytest
from test_comments import FakeTransport as CommentTransport
from test_comments import error_body, threads
from test_persistence import FakeDatabase, comment, meta

from research import runner
from research.comments import YouTubeComments
from research.metadata import YouTubeMetadata
from research.persistence import PersistenceError, ResearchRepository
from research.pipeline import (
    JobNotFoundError,
    JobNotRunnableError,
    run_research_job,
)
from research.search import YouTubeSearch
from research.transcripts import (
    TranscriptNetworkError,
    TranscriptsDisabledError,
    YouTubeTranscript,
    YouTubeTranscripts,
)
from research.youtube import (
    YouTubeClient,
    YouTubeQuotaError,
    YouTubeSearchResult,
)

JOB = "job-1"


class StubSearch:
    def __init__(self, ids, error=None):
        self.ids, self.error, self.calls = ids, error, []

    def search(self, query, *, max_candidates):
        self.calls.append((query, max_candidates))
        if self.error:
            raise self.error
        return [
            YouTubeSearchResult(i, f"u/{i}", "t", "c", "ch", datetime(2024, 1, 1))
            for i in self.ids
        ]


class StubMetadata:
    def __init__(self, unavailable=()):
        self.unavailable, self.calls = set(unavailable), []

    def get_metadata(self, ids):
        self.calls.append(list(ids))
        return [meta(i) for i in ids if i not in self.unavailable]


class StubTranscripts:
    def __init__(self, have=(), error=None):
        self.have, self.error, self.calls = set(have), error, []

    def get_transcripts(self, ids):
        self.calls.append(list(ids))
        if self.error:
            raise self.error
        return [YouTubeTranscript(i, f"transcript {i}", "en", False) for i in ids if i in self.have]


class StubComments:
    def __init__(self, per_video=2, skip=(), error=None):
        self.per_video, self.skip, self.error, self.calls = per_video, set(skip), error, []

    def get_comments(self, ids):
        self.calls.append(list(ids))
        if self.error:
            raise self.error
        return [comment(i, n) for i in ids if i not in self.skip
                for n in range(1, self.per_video + 1)]


def setup(status="queued", **row):
    db = FakeDatabase()
    db.tables["research_jobs"].append(
        {"id": JOB, "query": "UK sole trader accounting", "audience": "UK sole traders",
         "objective": "obj", "status": status, "started_at": None, "completed_at": None,
         "created_at": "2026-01-01T00:00:00+00:00", **row})
    return ResearchRepository(db), db


def run(repo, *, search=None, metadata=None, transcripts=None, comments=None, **kw):
    lines = []
    summary = run_research_job(
        JOB,
        repository=repo,
        search=search or StubSearch(["a", "b", "c"]),
        metadata=metadata or StubMetadata(),
        transcripts=transcripts or StubTranscripts(["a", "b"]),
        comments=comments or StubComments(),
        progress=lines.append,
        **kw,
    )
    return summary, lines


def job_row(db):
    [row] = db.tables["research_jobs"]
    return row


# --- validation ------------------------------------------------------------------------


def test_missing_job_fails_clearly_and_creates_nothing():
    repo, db = setup()
    db.tables["research_jobs"].clear()
    search = StubSearch(["a"])
    with pytest.raises(JobNotFoundError, match="does not exist"):
        run(repo, search=search)
    assert db.tables == {"research_jobs": [], "content": [], "comments": []}
    assert search.calls == []


@pytest.mark.parametrize("status", ["running", "completed", "failed", None, "weird"])
def test_non_queued_job_is_refused_untouched(status):
    repo, db = setup(status=status)
    search = StubSearch(["a"])
    before = dict(job_row(db))
    with pytest.raises(JobNotRunnableError, match="only queued jobs"):
        run(repo, search=search)
    assert job_row(db) == before
    assert search.calls == [] and db.tables["content"] == []


def test_job_without_query_is_refused_untouched():
    repo, db = setup(query="   ")
    with pytest.raises(JobNotRunnableError, match="no query"):
        run(repo)
    assert job_row(db)["status"] == "queued"


def test_losing_the_claim_race_runs_nothing():
    class Racy(ResearchRepository):
        def start_job(self, job_id):
            return False  # another process got there first

    repo, db = setup()
    racy = Racy(repo._db)
    search = StubSearch(["a"])
    with pytest.raises(JobNotRunnableError, match="no longer queued"):
        run(racy, search=search)
    assert search.calls == []
    assert job_row(db)["status"] == "queued"


# --- success -----------------------------------------------------------------------------


def test_successful_run_lifecycle_and_relationships():
    repo, db = setup()
    summary, lines = run(repo)
    row = job_row(db)
    assert row["id"] == JOB and len(db.tables["research_jobs"]) == 1  # no second job
    assert row["status"] == "completed"
    assert datetime.fromisoformat(row["started_at"]).tzinfo is not None
    assert datetime.fromisoformat(row["completed_at"]).tzinfo is not None
    assert (row["query"], row["audience"], row["objective"], row["created_at"]) == (
        "UK sole trader accounting", "UK sole traders", "obj", "2026-01-01T00:00:00+00:00")

    assert [c["research_job_id"] for c in db.tables["content"]] == [JOB] * 3
    assert [c["external_id"] for c in db.tables["content"]] == ["a", "b", "c"]
    content_id = {c["external_id"]: c["id"] for c in db.tables["content"]}
    assert len(db.tables["comments"]) == 6
    for c in db.tables["comments"]:
        assert c["content_id"] == content_id[c["external_id"].split("-")[0]]
    assert (summary.videos_found, summary.transcripts_retrieved,
            summary.transcripts_unavailable, summary.comments_retrieved) == (3, 2, 1, 6)
    assert summary.saved.content_inserted == 3 and summary.saved.comments_inserted == 6


def test_status_goes_queued_running_completed_in_order():
    seen = []

    class Db(FakeDatabase):
        def update(self, table, values, *, eq):
            if "status" in values:
                seen.append(values["status"])
            return super().update(table, values, eq=eq)

    repo, base = setup()
    db = Db()
    db.tables = base.tables
    run(ResearchRepository(db))
    assert seen == ["running", "completed"]


def test_search_uses_job_query_and_candidate_limit():
    repo, _ = setup()
    search = StubSearch(["a"])
    run(repo, search=search)
    assert search.calls == [("UK sole trader accounting", 10)]
    search = StubSearch(["a"])
    run(setup()[0], search=search, max_candidates=5)
    assert search.calls == [("UK sole trader accounting", 5)]


def test_services_receive_ids_in_search_order():
    repo, _ = setup()
    md, tr, cm = StubMetadata(), StubTranscripts(), StubComments()
    run(repo, search=StubSearch(["c", "a", "b"]), metadata=md, transcripts=tr, comments=cm)
    assert md.calls == tr.calls == cm.calls == [["c", "a", "b"]]


def test_progress_reports_each_stage_in_order():
    _, lines = run(setup()[0])
    text = "\n".join(lines)
    stages = ["queued → running", "Searching YouTube", "Found 3 videos", "Fetching metadata",
              "Fetching transcripts", "Retrieved 2 transcripts", "1 unavailable",
              "Fetching comments", "Retrieved 6 comments", "Saving results",
              "Saved 3 videos", "Saved 6 comments", "Job completed"]
    positions = [text.index(s) for s in stages]
    assert positions == sorted(positions)


# --- partial / unavailable data ------------------------------------------------------------


def test_missing_transcript_does_not_fail_job_and_stores_null():
    repo, db = setup()
    run(repo, transcripts=StubTranscripts(["a"]))
    assert job_row(db)["status"] == "completed"
    transcripts = {c["external_id"]: c["transcript"] for c in db.tables["content"]}
    assert transcripts == {"a": "transcript a", "b": None, "c": None}


def test_no_transcripts_at_all_still_completes():
    repo, db = setup()
    run(repo, transcripts=StubTranscripts([]))
    assert job_row(db)["status"] == "completed"
    assert all(c["transcript"] is None for c in db.tables["content"])


def test_unavailable_metadata_video_is_skipped_everywhere():
    repo, db = setup()
    md = StubMetadata(unavailable=["b"])
    tr, cm = StubTranscripts(), StubComments()
    run(repo, metadata=md, transcripts=tr, comments=cm)
    assert [c["external_id"] for c in db.tables["content"]] == ["a", "c"]
    assert tr.calls == cm.calls == [["a", "c"]]


def test_video_with_disabled_comments_is_saved_without_comments():
    repo, db = setup()
    transport = CommentTransport({
        "a": threads("a", 2),
        "b": error_body(403, "commentsDisabled", "disabled"),
        "c": threads("c", 1),
    })
    real_comments = YouTubeComments(YouTubeClient("k", transport=transport))
    summary, _ = run(repo, comments=real_comments)
    assert job_row(db)["status"] == "completed"
    assert len(db.tables["content"]) == 3
    by_content = {}
    content_id = {c["external_id"]: c["id"] for c in db.tables["content"]}
    for c in db.tables["comments"]:
        by_content.setdefault(c["content_id"], []).append(c["external_id"])
    assert by_content == {content_id["a"]: ["a-c1", "a-c2"], content_id["c"]: ["c-c1"]}
    assert summary.comments_retrieved == 3


def test_no_search_results_completes_with_no_content():
    repo, db = setup()
    summary, _ = run(repo, search=StubSearch([]))
    assert job_row(db)["status"] == "completed"
    assert db.tables["content"] == [] and summary.videos_found == 0


def test_transcript_disabled_error_semantics_come_from_the_real_service():
    class Provider:
        def fetch(self, video_id, language, allow_fallback):
            raise TranscriptsDisabledError("off")

    repo, db = setup()
    run(repo, transcripts=YouTubeTranscripts(Provider()))
    assert job_row(db)["status"] == "completed"
    assert all(c["transcript"] is None for c in db.tables["content"])


# --- fatal failures ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, error",
    [
        ({"search": StubSearch(["a"], error=YouTubeQuotaError("quota"))}, YouTubeQuotaError),
        ({"transcripts": StubTranscripts(error=TranscriptNetworkError("down"))},
         TranscriptNetworkError),
        ({"comments": StubComments(error=YouTubeQuotaError("quota"))}, YouTubeQuotaError),
        ({"search": StubSearch(["a"], error=RuntimeError("boom"))}, RuntimeError),
    ],
)
def test_fatal_error_marks_failed_and_reraises(kwargs, error):
    repo, db = setup()
    with pytest.raises(error):
        run(repo, **kwargs)
    row = job_row(db)
    assert row["status"] == "failed"
    assert row["completed_at"] is None
    assert row["started_at"] is not None  # it did start


def test_persistence_failure_marks_failed_keeps_partial_rows():
    repo, db = setup()
    db.fail_on = ("insert", "comments")
    with pytest.raises(PersistenceError):
        run(repo)
    row = job_row(db)
    assert row["status"] == "failed" and row["completed_at"] is None
    assert len(db.tables["content"]) == 3  # documented: not transactional


def test_keyboard_interrupt_does_not_leave_job_running():
    repo, db = setup()
    with pytest.raises(KeyboardInterrupt):
        run(repo, search=StubSearch(["a"], error=KeyboardInterrupt()))
    assert job_row(db)["status"] == "failed"


def test_failure_to_mark_failed_still_raises_original_error():
    class Db(FakeDatabase):
        def update(self, table, values, *, eq):
            if values.get("status") == "failed":
                raise PersistenceError("cannot update")
            return super().update(table, values, eq=eq)

    repo, base = setup()
    db = Db()
    db.tables = base.tables
    with pytest.raises(YouTubeQuotaError):
        run(ResearchRepository(db), search=StubSearch(["a"], error=YouTubeQuotaError("q")))


def test_failed_job_cannot_be_rerun():
    repo, db = setup()
    with pytest.raises(RuntimeError):
        run(repo, search=StubSearch(["a"], error=RuntimeError("boom")))
    with pytest.raises(JobNotRunnableError, match="'failed'"):
        run(repo)


def test_completed_job_cannot_be_rerun_and_adds_no_rows():
    repo, db = setup()
    run(repo)
    count = len(db.tables["content"])
    with pytest.raises(JobNotRunnableError):
        run(repo)
    assert len(db.tables["content"]) == count


# --- CLI ------------------------------------------------------------------------------------

GOOD_ID = "d8aee101-d48c-4be5-9247-57b33b56fe65"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-secret-not-real")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sr-secret-not-real")


def test_cli_rejects_non_uuid(capsys):
    assert runner.main(["--job-id", "abc"]) == 2
    assert "must be a UUID" in capsys.readouterr().err


def test_cli_requires_job_id():
    with pytest.raises(SystemExit) as info:
        runner.main([])
    assert info.value.code == 2


@pytest.mark.parametrize("missing", ["YOUTUBE_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
def test_cli_missing_config_exits_2_without_touching_anything(env, monkeypatch, capsys, missing):
    monkeypatch.delenv(missing)
    called = []
    monkeypatch.setattr(runner, "run_research_job", lambda *a, **k: called.append(1))
    assert runner.main(["--job-id", GOOD_ID]) == 2
    out = capsys.readouterr()
    assert missing.split("_")[0] in out.err and called == []
    assert "secret" not in out.out + out.err


def test_cli_success_exit_0_and_prints_progress_without_secrets(env, monkeypatch, capsys):
    repo, db = setup(id=GOOD_ID)
    monkeypatch.setattr(runner.ResearchRepository, "from_settings", lambda s: repo)
    monkeypatch.setattr(runner, "YouTubeSearch", lambda c: StubSearch(["a"]))
    monkeypatch.setattr(runner, "YouTubeMetadata", lambda c: StubMetadata())
    monkeypatch.setattr(runner, "YouTubeTranscripts", lambda: StubTranscripts(["a"]))
    monkeypatch.setattr(runner, "YouTubeComments", lambda c: StubComments())
    code = runner.main(["--job-id", GOOD_ID])
    out = capsys.readouterr()
    assert code == 0, out
    assert "Research Runner" in out.out and "Job completed" in out.out
    assert "secret" not in out.out + out.err
    assert job_row(db)["status"] == "completed"
    assert [c["research_job_id"] for c in db.tables["content"]] == [GOOD_ID]


def test_cli_job_error_exit_1(env, monkeypatch, capsys):
    def boom(*a, **k):
        raise JobNotRunnableError("is 'running'")

    monkeypatch.setattr(runner.ResearchRepository, "from_settings", lambda s: object())
    monkeypatch.setattr(runner, "run_research_job", boom)
    assert runner.main(["--job-id", GOOD_ID]) == 1
    assert "Cannot run job" in capsys.readouterr().err


def test_cli_fatal_failure_exit_3_without_secrets(env, monkeypatch, capsys):
    def boom(*a, **k):
        raise YouTubeQuotaError("Quota or rate limit exceeded")

    monkeypatch.setattr(runner.ResearchRepository, "from_settings", lambda s: object())
    monkeypatch.setattr(runner, "run_research_job", boom)
    assert runner.main(["--job-id", GOOD_ID]) == 3
    out = capsys.readouterr()
    assert "YouTubeQuotaError" in out.err and "secret" not in out.err
