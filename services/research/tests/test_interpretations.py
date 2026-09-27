"""Append-only interpretations: persistence behaviour and the migration's schema."""

import re
from pathlib import Path

import pytest
from test_pipeline import A, B, C, FIELDS, JOB, make_result, run, setup

from research.classification import CLASSIFICATION_SCHEMA_VERSION, FieldResult
from research.persistence import PersistenceError

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
KW = dict(model="model-a", prompt_version="prompt-v1")


def stored():
    """A job whose raw content is saved (via a run) but with interpretations cleared."""
    repo, db = setup()
    run(repo)
    db.tables["interpretations"].clear()
    return repo, db


def rows(db):
    return db.tables["interpretations"]


# --- persistence --------------------------------------------------------------------------


def test_one_classification_creates_one_interpretation_with_full_result():
    repo, db = stored()
    result = make_result(pain_point=FieldResult(None))
    summary = repo.save_classifications(JOB, {A: result}, **KW)
    assert (summary.inserted, summary.skipped) == (1, 0)
    [row] = rows(db)
    content = {c["external_id"]: c["id"] for c in db.tables["content"]}
    assert row["content_id"] == content[A]
    assert row["analysis_type"] == "classification"
    assert row["model"] == "model-a" and row["prompt_version"] == "prompt-v1"
    assert row["schema_version"] == CLASSIFICATION_SCHEMA_VERSION == "classification-v1"
    assert set(row["result"]) == set(FIELDS)
    assert row["result"]["hook"] == {"value": "hook value", "confidence": 0.8, "evidence": "hook evidence"}
    assert row["result"]["pain_point"] == {"value": None, "confidence": None, "evidence": None}
    assert row["result"] == result.to_dict()


def test_explicit_schema_version_is_persisted():
    repo, db = stored()
    repo.save_classifications(JOB, {A: make_result()}, schema_version="classification-v9", **KW)
    assert rows(db)[0]["schema_version"] == "classification-v9"


def test_other_model_and_newer_prompt_version_append_new_interpretations():
    repo, db = stored()
    repo.save_classifications(JOB, {A: make_result()}, **KW)
    snapshot = [dict(r) for r in rows(db)]
    repo.save_classifications(JOB, {A: make_result(topic=FieldResult("b", 0.1, "e"))},
                              model="model-b", prompt_version="prompt-v1")
    repo.save_classifications(JOB, {A: make_result()}, model="model-a", prompt_version="prompt-v2")
    assert len(rows(db)) == 3
    assert rows(db)[:1] == snapshot  # the earlier interpretation is untouched
    assert {(r["model"], r["prompt_version"]) for r in rows(db)} == {
        ("model-a", "prompt-v1"), ("model-b", "prompt-v1"), ("model-a", "prompt-v2")}
    assert len({r["content_id"] for r in rows(db)}) == 1  # all on the same content


def test_new_schema_version_appends_too():
    repo, db = stored()
    repo.save_classifications(JOB, {A: make_result()}, **KW)
    repo.save_classifications(JOB, {A: make_result()}, schema_version="classification-v2", **KW)
    assert len(rows(db)) == 2


def test_rerunning_the_exact_same_operation_adds_nothing_and_never_overwrites():
    repo, db = stored()
    repo.save_classifications(JOB, {A: make_result(), B: make_result()}, **KW)
    before = [dict(r) for r in rows(db)]
    again = repo.save_classifications(
        JOB, {A: make_result(topic=FieldResult("different", 0.2, "e")), B: make_result(), C: make_result()}, **KW)
    assert (again.inserted, again.skipped) == (1, 2)  # only C is new
    assert rows(db)[:2] == before  # first wins; A's result was NOT replaced
    assert len(rows(db)) == 3


def test_missing_content_fails_cleanly_and_writes_nothing():
    repo, db = stored()
    with pytest.raises(PersistenceError, match="No content rows"):
        repo.save_classifications(JOB, {A: make_result(), "zzzzzzzzzzz": make_result()}, **KW)
    assert rows(db) == []
    with pytest.raises(PersistenceError, match="No content rows"):
        repo.save_classifications("other-job", {A: make_result()}, **KW)


def test_empty_results_write_nothing():
    repo, db = stored()
    assert repo.save_classifications(JOB, {}, **KW).inserted == 0 and rows(db) == []


def test_no_old_content_write_occurs():
    repo, db = stored()
    before = [dict(c) for c in db.tables["content"]]
    calls = len(db.calls)
    repo.save_classifications(JOB, {A: make_result()}, **KW)
    assert db.tables["content"] == before  # unchanged, incl. no `classification` key
    assert not any(c[0] in ("update", "insert") and c[1] == "content" for c in db.calls[calls:])


# --- migration ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sql():
    [path] = MIGRATIONS.glob("*_create_interpretations.sql")
    return re.sub(r"--[^\n]*", "", path.read_text())  # comments stripped


def column(sql, name):
    m = re.search(rf"^\s*{name}\s+([^\n]*?),?\s*$", sql, re.M)
    assert m, name
    return m.group(1).lower()


def test_migration_creates_the_table_with_required_typed_columns(sql):
    assert "create table if not exists public.interpretations" in sql
    assert "uuid primary key default gen_random_uuid()" in column(sql, "id")
    for name in ("analysis_type", "model", "prompt_version", "schema_version"):
        assert column(sql, name) == "text not null", name
    assert column(sql, "result") == "jsonb not null"
    assert column(sql, "created_at") == "timestamptz not null default now()"


def test_migration_has_fk_to_content_with_defined_delete_behaviour(sql):
    body = column(sql, "content_id")
    assert body.startswith("uuid not null")
    fk = re.search(r"content_id\s+uuid not null\s+references public\.content \(id\)([^,]*)", sql)
    assert fk and "on delete cascade" in fk.group(1)


def test_migration_indexes_content_id_and_allows_many_interpretations(sql):
    assert "create index if not exists interpretations_content_id_idx" in sql
    assert "on public.interpretations (content_id)" in sql
    assert "unique" not in sql.lower()  # no constraint limiting interpretations per content


def test_migration_preserves_the_smoke_result_before_dropping_the_old_column(sql):
    insert = sql.index("insert into public.interpretations")
    check = sql.index("raise exception")
    drop = sql.index("drop column if exists classification")
    assert insert < check < drop
    assert "2d92a734-5efe-43fc-a58f-53c33d23acb6" in sql
    assert "claude-haiku-4-5-20251001" in sql and "classification-v2" in sql


# --- legacy column drop migration ---------------------------------------------------------

LEGACY = ["topic", "audience", "pain_point", "hook", "hook_type", "format", "emotion", "cta"]
KEPT = ["transcript", "title", "description", "views", "likes", "comments_count", "url",
        "creator", "published_at", "duration_seconds", "external_id", "platform"]


@pytest.fixture(scope="module")
def drop_sql():
    [path] = MIGRATIONS.glob("*_drop_legacy_classification_columns_from_content.sql")
    return re.sub(r"--[^\n]*", "", path.read_text())


def test_drop_migration_removes_exactly_the_eight_legacy_columns(drop_sql):
    dropped = re.findall(r"drop column if exists (\w+)", drop_sql)
    assert sorted(dropped) == sorted(LEGACY)
    assert "alter table public.content" in drop_sql
    for name in KEPT + ["interpretations", "comments"]:
        assert not re.search(rf"drop column[^;]*\b{name}\b", drop_sql), name
    assert "drop table" not in drop_sql.lower()


def test_drop_migration_refuses_to_lose_data_and_leaves_rls_alone(drop_sql):
    guard, drop = drop_sql.index("raise exception"), drop_sql.index("alter table public.content")
    assert guard < drop
    assert "interpretations" in drop_sql[:drop]  # guard requires an interpretation to exist
    assert "policy" not in drop_sql.lower() and "row level security" not in drop_sql.lower()


def test_interpretations_migration_is_unmodified_and_still_drops_only_classification():
    text = next(MIGRATIONS.glob("*_create_interpretations.sql")).read_text()
    assert "drop column if exists classification;" in text
    assert not any(f"drop column if exists {c}" in text for c in LEGACY)


def test_migrations_apply_in_order():
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    create = next(i for i, n in enumerate(names) if "create_interpretations" in n)
    drop = next(i for i, n in enumerate(names) if "drop_legacy_classification" in n)
    assert create < drop


def test_full_pipeline_writes_no_legacy_column_anywhere():
    repo, db = setup()
    run(repo)
    for row in db.tables["content"]:
        assert not set(LEGACY) & set(row) and "classification" not in row
    assert len(rows(db)) == 3  # classification landed in interpretations only
