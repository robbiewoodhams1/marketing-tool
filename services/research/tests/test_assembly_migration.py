import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


@pytest.fixture(scope="module")
def sql():
    [path] = MIGRATIONS.glob("*_create_media_assemblies.sql")
    stripped = re.sub(r"--[^\n]*", "", path.read_text())
    return re.sub(r"\s+", " ", stripped).lower()


def test_migration_creates_an_immutable_assemblies_table_with_a_format_checked_run_key(sql):
    assert "create table public.media_assemblies" in sql
    body = sql.split("create table public.media_assemblies")[1].split("comment on table")[0]
    for col in (
        "production_id uuid not null", "run_key text not null", "status text not null",
        "source_asset_ids uuid[] not null", "source_scene_count integer not null",
    ):
        assert col in body, col
    assert "run_key ~ '^[0-9a-f]{64}$'" in body
    assert "create trigger media_assemblies_immutable before update" in sql


def test_migration_only_guarantees_uniqueness_for_completed_assemblies_not_failed_retries(sql):
    # A failed attempt must remain retryable (its row is kept, not unique) -
    # only a completed assembly is guaranteed unique per run_key.
    assert "constraint media_assemblies_run_key_key unique (run_key)" not in sql
    assert (
        "create unique index media_assemblies_completed_run_key_key "
        "on public.media_assemblies (run_key) where status = 'completed'" in sql
    )


def test_migration_enforces_completed_xor_failed(sql):
    body = sql.split("create table public.media_assemblies")[1].split("comment on table")[0]
    assert "status in ('completed', 'failed')" in body
    assert "media_assemblies_completed_has_output_no_error" in body
    # completed requires output + completed_at + no error; failed requires error + no output/completed_at
    assert "output_storage_path is not null" in body
    assert "completed_at is not null" in body
    assert "output_storage_path is null" in body
    assert "completed_at is null" in body


def test_migration_source_asset_ids_cannot_be_empty(sql):
    body = sql.split("create table public.media_assemblies")[1].split("comment on table")[0]
    assert "array_length(source_asset_ids, 1) > 0" in body


def test_migration_has_a_gin_index_on_source_asset_ids_and_a_production_index(sql):
    assert "create index media_assemblies_production_id_idx on public.media_assemblies (production_id)" in sql
    assert "create index media_assemblies_source_asset_ids_idx on public.media_assemblies using gin (source_asset_ids)" in sql


def test_migration_only_grants_dashboard_read_access(sql):
    policies = re.findall(r"create policy [^;]+;", sql)
    assert len(policies) == 1
    assert "for select to anon, authenticated using (true)" in policies[0]
    assert not re.search(r"for (insert|update|delete|all)", sql)


def test_migration_writes_through_one_atomic_function_only_the_service_role_can_call(sql):
    assert "create function public.save_media_assembly(payload jsonb) returns jsonb" in sql
    assert "revoke all on function public.save_media_assembly(jsonb) from public, anon, authenticated" in sql
    assert "grant execute on function public.save_media_assembly(jsonb) to service_role" in sql


def test_migration_never_invents_a_processing_cost(sql):
    # No cost_amount/cost_currency column on this table - Assembly makes no
    # AI provider call of its own (see the module docstring).
    body = sql.split("create table public.media_assemblies")[1].split("comment on table")[0]
    assert "cost_amount" not in body and "cost_currency" not in body


def test_this_is_the_latest_migration():
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert names[-1] == next(n for n in names if "add_audio_voice_generation" in n)
