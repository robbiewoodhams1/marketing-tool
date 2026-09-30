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
    assert names[-1] == next(n for n in names if "add_captions_v1" in n)


# --- Assembly V2 (voiceover sync) migration -----------------------------------------------------------


@pytest.fixture(scope="module")
def v2_sql():
    [path] = MIGRATIONS.glob("*_add_assembly_v2_audio_sync.sql")
    stripped = re.sub(r"--[^\n]*", "", path.read_text())
    return re.sub(r"\s+", " ", stripped).lower()


def test_v2_migration_adds_voice_asset_ids_and_policy_and_manifest_columns(v2_sql):
    assert "add column if not exists source_voice_asset_ids uuid[]" in v2_sql
    assert "add column if not exists audio_policy text not null default 'mute-v1'" in v2_sql
    assert "add column if not exists scene_manifest jsonb" in v2_sql


def test_v2_migration_constrains_audio_policy_to_known_values(v2_sql):
    assert "audio_policy in ('mute-v1', 'voiceover-sync-v1')" in v2_sql


def test_v2_migration_requires_one_voice_asset_id_per_scene_when_present(v2_sql):
    assert (
        "check (source_voice_asset_ids is null or array_length(source_voice_asset_ids, 1) = source_scene_count)"
        in v2_sql
    )


def test_v2_migration_recreates_save_media_assembly_with_the_new_fields(v2_sql):
    assert "create or replace function public.save_media_assembly(payload jsonb) returns jsonb" in v2_sql
    assert "source_voice_asset_ids" in v2_sql and "scene_manifest" in v2_sql
    assert "revoke all on function public.save_media_assembly(jsonb) from public, anon, authenticated" in v2_sql
    assert "grant execute on function public.save_media_assembly(jsonb) to service_role" in v2_sql


# --- Captions V1 migration -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def captions_sql():
    [path] = MIGRATIONS.glob("*_add_captions_v1.sql")
    stripped = re.sub(r"--[^\n]*", "", path.read_text())
    return re.sub(r"\s+", " ", stripped).lower()


def test_captions_migration_adds_nullable_policy_and_manifest_columns(captions_sql):
    assert "add column if not exists caption_policy text" in captions_sql
    assert "add column if not exists caption_manifest jsonb" in captions_sql
    # Unlike audio_policy, captions are optional - no NOT NULL/default here.
    assert "caption_policy text not null" not in captions_sql


def test_captions_migration_constrains_caption_policy_to_known_values(captions_sql):
    assert "caption_policy is null or caption_policy in ('captions-burned-v1')" in captions_sql


def test_captions_migration_requires_manifest_and_policy_together(captions_sql):
    assert "(caption_manifest is null) = (caption_policy is null)" in captions_sql


def test_captions_migration_recreates_save_media_assembly_with_the_new_fields(captions_sql):
    assert "create or replace function public.save_media_assembly(payload jsonb) returns jsonb" in captions_sql
    assert "caption_policy" in captions_sql and "caption_manifest" in captions_sql
    assert "nullif(payload -> 'caption_manifest', 'null'::jsonb)" in captions_sql
    assert "revoke all on function public.save_media_assembly(jsonb) from public, anon, authenticated" in captions_sql
    assert "grant execute on function public.save_media_assembly(jsonb) to service_role" in captions_sql
