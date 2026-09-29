import copy
import re
from pathlib import Path

import pytest
from media_fixtures import (
    PRODUCTION_ID,
    FakeImageProvider,
    FakeStorage,
    good_image,
    production_row,
    scene_row,
    seeded_db,
)

from research import media
from research.media import MediaSourceError, run_media_generation
from research.media_providers import MediaProviderAPIError, MediaProviderNetworkError
from research.media_schema import MediaValidationError
from research.media_store import MediaRepository, compute_run_key
from research.persistence import PersistenceError

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def run(db, image_provider=None, storage=None, **kw):
    lines = []
    outcome = run_media_generation(
        PRODUCTION_ID, repository=MediaRepository(db), image_provider=image_provider or FakeImageProvider(),
        storage=storage or FakeStorage(), progress=lines.append, **kw
    )
    return outcome, lines


def snapshot(db):
    return copy.deepcopy(db.tables)


# --- run identity ------------------------------------------------------------------------------


def key(**kw):
    return compute_run_key(**{
        "production_id": PRODUCTION_ID, "image_provider": "gemini", "image_model": "m",
        "video_provider": None, "video_model": None, "pack_version": "media-pack-v1", **kw,
    })


def test_run_key_is_a_sha256_hex_digest_and_stable():
    k = key()
    assert re.fullmatch(r"[0-9a-f]{64}", k) and k == key()


@pytest.mark.parametrize("change", [
    {"image_model": "other-model"}, {"video_provider": "veo"}, {"video_model": "veo-3.1"},
    {"pack_version": "media-pack-v2"}, {"schema_version": "media-v2"}, {"production_id": "prod-2"},
])
def test_changing_any_configuration_input_changes_the_run_key(change):
    assert key(**change) != key()


# --- the happy path (all scenes succeed) --------------------------------------------------------


def test_a_complete_run_generates_and_persists_every_scene():
    db = seeded_db()
    storage = FakeStorage()
    outcome, lines = run(db, storage=storage)

    assert outcome.status == "completed"
    assert outcome.completed_scenes == (1, 2) and outcome.failed_scenes == {}

    [r] = db.tables["media_runs"]
    assert r["production_id"] == PRODUCTION_ID and r["image_provider"] == "gemini"
    assert re.fullmatch(r"[0-9a-f]{64}", r["run_key"]) and r["run_key"] == outcome.run_key
    assert r["scene_count"] == 2

    assets = db.tables["media_assets"]
    assert len(assets) == 2 and {a["scene_number"] for a in assets} == {1, 2}
    for a in assets:
        assert a["status"] == "completed" and a["storage_path"] and a["error_message"] is None
        assert a["media_run_id"] == r["id"] and a["production_id"] == PRODUCTION_ID
    assert len(storage.uploads) == 2
    assert any("generated" in line for line in lines)


def test_the_production_is_never_modified():
    db = seeded_db()
    before = copy.deepcopy(db.tables["productions"])
    run(db)
    assert db.tables["productions"] == before
    assert not any(c[0] == "update" for c in db.calls)


def test_scene_prompt_and_capability_refs_are_persisted_per_asset():
    db = seeded_db()
    run(db)
    by_scene = {a["scene_number"]: a for a in db.tables["media_assets"]}
    assert by_scene[1]["product_capability_refs"] == ["quotes"]
    assert by_scene[2]["product_capability_refs"] == ["reusable_templates"]
    assert "Scene 1 purpose" in by_scene[1]["prompt"]


# --- scene selection -------------------------------------------------------------------------


def test_a_single_requested_scene_generates_only_that_one():
    db = seeded_db()
    outcome, _ = run(db, scenes=[1])
    assert outcome.completed_scenes == (1,) and len(db.tables["media_assets"]) == 1


def test_an_unknown_requested_scene_is_refused_before_any_work():
    db = seeded_db()
    image_provider = FakeImageProvider()
    with pytest.raises(MediaSourceError, match=r"scene\(s\) \[99\]"):
        run(db, image_provider=image_provider, scenes=[99])
    assert image_provider.calls == [] and db.tables["media_assets"] == []


# --- partial success: one scene fails, others still persist -------------------------------------


def test_one_scene_failing_does_not_discard_the_others():
    image_provider = FakeImageProvider(side_effects=[
        good_image(), MediaProviderAPIError("HTTP 500: rate limited", status=500),
    ])
    db = seeded_db()
    outcome, lines = run(db, image_provider=image_provider)

    assert outcome.status == "partial"
    assert outcome.completed_scenes == (1,) and outcome.failed_scenes == {2: "HTTP 500: rate limited"}
    [ok] = [a for a in db.tables["media_assets"] if a["scene_number"] == 1]
    [bad] = [a for a in db.tables["media_assets"] if a["scene_number"] == 2]
    assert ok["status"] == "completed" and bad["status"] == "failed"
    assert bad["storage_path"] is None and bad["error_message"]
    assert any("failed" in line for line in lines)


def test_every_scene_failing_is_reported_as_failed_status():
    image_provider = FakeImageProvider(error=MediaProviderNetworkError("down"))
    db = seeded_db()
    outcome, _ = run(db, image_provider=image_provider)
    assert outcome.status == "failed" and outcome.completed_scenes == ()
    assert len(outcome.failed_scenes) == 2
    assert all(a["status"] == "failed" for a in db.tables["media_assets"])


def test_a_storage_failure_is_recorded_as_a_failed_asset_not_a_crash():
    image_provider = FakeImageProvider()
    db = seeded_db()
    outcome, _ = run(db, image_provider=image_provider, storage=FakeStorage(fail=True), scenes=[1])
    assert outcome.status == "failed" and 1 in outcome.failed_scenes
    [asset] = db.tables["media_assets"]
    assert asset["status"] == "failed" and "simulated storage failure" in asset["error_message"]


# --- idempotency: a completed scene is never regenerated ----------------------------------------


def test_a_rerun_skips_already_completed_scenes_and_does_not_call_the_provider_again():
    db = seeded_db()
    run(db)  # both scenes complete
    image_provider = FakeImageProvider()
    outcome, lines = run(db, image_provider=image_provider)
    assert outcome.status == "completed"
    assert outcome.already_completed_scenes == (1, 2) and outcome.completed_scenes == ()
    assert image_provider.calls == []  # no re-call, no re-charge
    assert len(db.tables["media_assets"]) == 2  # nothing duplicated
    assert any("already generated" in line for line in lines)


def test_a_rerun_retries_only_the_previously_failed_scene():
    db = seeded_db()
    run(db, image_provider=FakeImageProvider(side_effects=[
        good_image(), MediaProviderAPIError("HTTP 500", status=500),
    ]))
    assert len(db.tables["media_assets"]) == 2  # one completed, one failed

    retry_provider = FakeImageProvider()  # succeeds this time
    outcome, _ = run(db, image_provider=retry_provider)
    assert outcome.status == "completed"
    assert outcome.completed_scenes == (2,) and outcome.already_completed_scenes == (1,)
    assert len(retry_provider.calls) == 1  # only scene 2 was retried
    assert len(db.tables["media_assets"]) == 3  # the old failed row is kept, a new completed one is added
    scene_2_rows = [a for a in db.tables["media_assets"] if a["scene_number"] == 2]
    assert len(scene_2_rows) == 2 and {r["status"] for r in scene_2_rows} == {"failed", "completed"}


def test_a_changed_model_creates_a_new_run_and_regenerates_everything():
    db = seeded_db()
    run(db, image_provider=FakeImageProvider(model="model-a"))
    outcome, _ = run(db, image_provider=FakeImageProvider(model="model-b"))
    assert outcome.status == "completed" and outcome.completed_scenes == (1, 2)
    assert len(db.tables["media_runs"]) == 2 and len(db.tables["media_assets"]) == 4


# --- atomic persistence per asset -----------------------------------------------------------------


def test_the_database_uniqueness_is_the_final_guard_for_runs():
    db = seeded_db()
    from research.media_store import build_run_payload

    payload = build_run_payload(
        production_id=PRODUCTION_ID, image_provider="gemini", image_model="m", video_provider=None,
        video_model=None, run_key=key(), scene_count=2, manifest={}, pack_version="media-pack-v1",
    )
    repo = MediaRepository(db)
    first = repo.save_run(payload)
    assert first.status == "created"
    again = repo.save_run(payload)
    assert again.status == "exists" and again.run_id == first.run_id
    assert len(db.tables["media_runs"]) == 1


def test_save_asset_rejects_an_asset_for_an_unknown_run():
    db = seeded_db()
    from research.media_schema import AssetStatus, AssetType, GeneratedAsset

    asset = GeneratedAsset(
        scene_number=1, asset_type=AssetType.IMAGE, status=AssetStatus.FAILED, provider="gemini",
        model="m", prompt="p", generation_config={}, product_capability_refs=(), error_message="e",
    )
    with pytest.raises(PersistenceError, match="does not exist"):
        MediaRepository(db).save_asset(media_run_id="nope", production_id=PRODUCTION_ID, asset=asset)


# --- refusal before any work -----------------------------------------------------------------------


def test_production_not_found_is_refused_before_any_work():
    db = seeded_db()
    image_provider = FakeImageProvider()
    with pytest.raises(MediaSourceError, match="does not exist"):
        run_media_generation("nope", repository=MediaRepository(db), image_provider=image_provider, storage=FakeStorage())
    assert image_provider.calls == [] and db.tables["media_runs"] == []


def test_an_invalid_asset_from_validation_is_a_hard_failure_not_silently_dropped():
    # A defence-in-depth scenario: if a scene somehow cited a capability
    # outside its own production scene, validate_asset must reject it loudly
    # rather than let a bad row through. Simulated by tampering with the
    # provider-return path indirectly is awkward, so this exercises
    # research.media_validation directly through the same call the service uses.
    from research.media_pack import build_media_scene_pack
    from research.media_validation import validate_asset
    from research.media_schema import AssetStatus, AssetType, GeneratedAsset
    from opportunity_fixtures import PRODUCT

    pack = build_media_scene_pack(production=production_row(), scene=scene_row(1), product=PRODUCT)
    bad_asset = GeneratedAsset(
        scene_number=1, asset_type=AssetType.IMAGE, status=AssetStatus.COMPLETED, provider="gemini",
        model="m", prompt=pack.prompt, generation_config={}, product_capability_refs=("reusable_templates",),
        storage_bucket="b", storage_path="p", storage_url="https://x", mime_type="image/png",
    )
    with pytest.raises(MediaValidationError, match="does not use in the production"):
        validate_asset(bad_asset, production=production_row())


# --- CLI --------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-not-real")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sr-secret-not-real")


def test_cli_missing_configuration_exits_2_without_doing_anything(env, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY")
    called = []
    monkeypatch.setattr(media, "run_media_generation", lambda *a, **k: called.append(1))
    assert media.main(["--production-id", "8e610e89-87e7-4838-a7f7-222d7fceda0b"]) == 2
    out = capsys.readouterr()
    assert called == [] and "secret" not in out.out + out.err


def test_cli_missing_supabase_config_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert media.main(["--production-id", "8e610e89-87e7-4838-a7f7-222d7fceda0b"]) == 2


def test_cli_rejects_an_unimplemented_image_provider(env, monkeypatch, capsys):
    monkeypatch.setenv("MEDIA_IMAGE_PROVIDER", "dalle")
    assert media.main(["--production-id", "8e610e89-87e7-4838-a7f7-222d7fceda0b"]) == 2
    assert "not implemented" in capsys.readouterr().err


GOOD_PRODUCTION = "8e610e89-87e7-4838-a7f7-222d7fceda0b"


def cli(monkeypatch, db, image_provider, extra_args=()):
    monkeypatch.setattr(media, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(media, "_resolve_image_provider", lambda settings: image_provider)
    monkeypatch.setattr(media, "SupabaseStorage", lambda *a, **k: FakeStorage())
    return media.main(["--production-id", GOOD_PRODUCTION, *extra_args])


def cli_db():
    db = seeded_db()
    db.tables["productions"][0]["id"] = GOOD_PRODUCTION
    return db


def test_cli_success_prints_progress_and_no_secrets(env, monkeypatch, capsys):
    db = cli_db()
    assert cli(monkeypatch, db, FakeImageProvider()) == 0
    out = capsys.readouterr()
    assert "Media Generation" in out.out and "Media run completed" in out.out
    assert "secret" not in out.out + out.err
    assert len(db.tables["media_assets"]) == 2


def test_cli_respects_the_scene_flag(env, monkeypatch, capsys):
    db = cli_db()
    assert cli(monkeypatch, db, FakeImageProvider(), extra_args=["--scene", "1"]) == 0
    assert len(db.tables["media_assets"]) == 1


def test_cli_unknown_production_exits_1(env, monkeypatch, capsys):
    db = cli_db()
    db.tables["productions"] = []
    assert cli(monkeypatch, db, FakeImageProvider()) == 1
    assert "Cannot generate media" in capsys.readouterr().err


def test_cli_all_scenes_failing_exits_3_and_reports_each_failure(env, monkeypatch, capsys):
    db = cli_db()
    assert cli(monkeypatch, db, FakeImageProvider(error=MediaProviderAPIError("HTTP 500", status=500))) == 3
    err = capsys.readouterr().err
    assert "scene 1:" in err and "scene 2:" in err


def test_cli_partial_success_still_exits_0(env, monkeypatch, capsys):
    db = cli_db()
    provider = FakeImageProvider(side_effects=[good_image(), MediaProviderAPIError("HTTP 500", status=500)])
    assert cli(monkeypatch, db, provider) == 0
    out = capsys.readouterr()
    assert "partial" in out.out


# --- migration ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sql():
    [path] = MIGRATIONS.glob("*_create_media_runs_and_media_assets.sql")
    stripped = re.sub(r"--[^\n]*", "", path.read_text())
    return re.sub(r"\s+", " ", stripped).lower()


def test_migration_creates_the_storage_bucket(sql):
    assert "insert into storage.buckets (id, name, public)" in sql
    assert "'media-assets', 'media-assets', true" in sql
    assert "on conflict (id) do nothing" in sql


def test_migration_creates_an_immutable_run_table_with_a_unique_run_key(sql):
    assert "create table public.media_runs" in sql
    for col in ("production_id uuid not null", "image_provider text not null", "image_model text not null",
                "pack_version text not null", "schema_version text not null", "run_key text not null",
                "scene_count integer not null", "input_manifest jsonb not null"):
        assert col in sql, col
    assert "constraint media_runs_run_key_key unique (run_key)" in sql
    assert "run_key ~ '^[0-9a-f]{64}$'" in sql
    assert "unique (id, production_id)" in sql
    assert "create trigger media_runs_immutable before update" in sql


def test_migration_media_assets_enforces_completed_xor_failed(sql):
    body = sql.split("create table public.media_assets")[1].split("comment on table")[0]
    assert "asset_type in ('image', 'video')" in body
    assert "status in ('completed', 'failed')" in body
    assert "media_assets_completed_has_storage_no_error" in body
    assert "create trigger media_assets_immutable before update" in sql


def test_migration_only_grants_dashboard_read_access(sql):
    policies = re.findall(r"create policy [^;]+;", sql)
    assert len(policies) == 2
    for p in policies:
        assert "for select to anon, authenticated using (true)" in p
    assert not re.search(r"for (insert|update|delete|all)", sql)


def test_migration_writes_through_two_atomic_functions_only_the_service_role_can_call(sql):
    assert "create function public.save_media_run(payload jsonb) returns jsonb" in sql
    assert "create function public.save_media_asset(payload jsonb) returns jsonb" in sql
    assert "on conflict (run_key) do nothing" in sql
    assert "revoke all on function public.save_media_run(jsonb) from public, anon, authenticated" in sql
    assert "revoke all on function public.save_media_asset(jsonb) from public, anon, authenticated" in sql
    assert "grant execute on function public.save_media_run(jsonb) to service_role" in sql
    assert "grant execute on function public.save_media_asset(jsonb) to service_role" in sql


def test_this_is_the_latest_migration():
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert names[-1] == next(n for n in names if "create_media_runs" in n)
