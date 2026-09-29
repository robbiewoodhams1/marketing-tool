"""Media Generation V2: video generation through `run_media_generation` /
`research.media`'s CLI.

Mirrors `test_media_service.py`'s image-path tests exactly where the same
guarantee applies to video (partial success, idempotency, no secrets logged),
plus the guarantees specific to adding a second media type: a run's identity
reflects only the modality actually used, and it is impossible to spend
video credits (or vice versa) without explicitly asking for `--media-type video`.
"""

from __future__ import annotations

import pytest
from media_fixtures import (
    PRODUCTION_ID,
    FakeImageProvider,
    FakeStorage,
    FakeVideoProvider,
    good_video,
    seeded_db,
)

from research import media
from research.media import run_media_generation
from research.media_providers import MediaProviderAPIError
from research.media_schema import AssetType
from research.media_store import MediaRepository, compute_run_key

GOOD_PRODUCTION = "8e610e89-87e7-4838-a7f7-222d7fceda0b"


def run_video(db, video_provider=None, storage=None, **kw):
    lines = []
    outcome = run_media_generation(
        PRODUCTION_ID, repository=MediaRepository(db), media_type=AssetType.VIDEO,
        video_provider=video_provider or FakeVideoProvider(), storage=storage or FakeStorage(),
        progress=lines.append, **kw,
    )
    return outcome, lines


# --- run identity: video and image runs never share or leak each other's provider ------------------


def test_an_image_run_and_a_video_run_for_the_same_production_get_different_run_keys():
    image_key = compute_run_key(
        production_id=PRODUCTION_ID, image_provider="gemini", image_model="m", video_provider=None,
        video_model=None, pack_version="media-pack-v1",
    )
    video_key = compute_run_key(
        production_id=PRODUCTION_ID, image_provider=None, image_model=None, video_provider="veo",
        video_model="m", pack_version="media-pack-v1",
    )
    assert image_key != video_key


def test_a_video_run_records_only_the_video_provider_never_an_image_one():
    db = seeded_db()
    run_video(db, video_provider=FakeVideoProvider(model="veo-3.1-fast-generate-preview"))
    [r] = db.tables["media_runs"]
    assert r["video_provider"] == "veo" and r["video_model"] == "veo-3.1-fast-generate-preview"
    assert r["image_provider"] is None and r["image_model"] is None


def test_an_image_run_records_only_the_image_provider_never_a_video_one():
    db = seeded_db()
    run_media_generation(
        PRODUCTION_ID, repository=MediaRepository(db), image_provider=FakeImageProvider(), storage=FakeStorage(),
    )
    [r] = db.tables["media_runs"]
    assert r["image_provider"] == "gemini" and r["image_model"] == FakeImageProvider().model
    assert r["video_provider"] is None and r["video_model"] is None


def test_running_video_and_image_for_the_same_production_creates_two_independent_runs():
    db = seeded_db()
    run_video(db)
    run_media_generation(
        PRODUCTION_ID, repository=MediaRepository(db), image_provider=FakeImageProvider(), storage=FakeStorage(),
    )
    assert len(db.tables["media_runs"]) == 2
    assert len(db.tables["media_assets"]) == 4  # 2 scenes x 2 runs
    kinds = {a["asset_type"] for a in db.tables["media_assets"]}
    assert kinds == {"image", "video"}


def test_generate_video_requires_a_video_provider():
    db = seeded_db()
    with pytest.raises(ValueError, match="video_provider is required"):
        run_media_generation(
            PRODUCTION_ID, repository=MediaRepository(db), storage=FakeStorage(), media_type=AssetType.VIDEO
        )


def test_generate_image_requires_an_image_provider():
    db = seeded_db()
    with pytest.raises(ValueError, match="image_provider is required"):
        run_media_generation(PRODUCTION_ID, repository=MediaRepository(db), storage=FakeStorage())


# --- the happy path ------------------------------------------------------------------------------


def test_a_complete_video_run_generates_and_persists_every_scene():
    db = seeded_db()
    storage = FakeStorage()
    outcome, lines = run_video(db, storage=storage)

    assert outcome.status == "completed"
    assert outcome.completed_scenes == (1, 2) and outcome.failed_scenes == {}
    assets = db.tables["media_assets"]
    assert len(assets) == 2
    for a in assets:
        assert a["asset_type"] == "video" and a["provider"] == "veo"
        assert a["status"] == "completed" and a["mime_type"] == "video/mp4"
        assert a["duration_seconds"] == 8.0
    assert len(storage.uploads) == 2
    assert all(path.endswith(".mp4") for path, _, _ in storage.uploads)
    assert any("video generated" in line for line in lines)


def test_the_persisted_duration_is_what_was_actually_requested_of_the_provider_not_the_scenes_own():
    # scene_row() fixtures set duration_seconds=6, which Veo does support, so
    # this asserts the persisted value tracks the PROVIDER's return
    # (video.duration_seconds), never silently copied from the scene.
    db = seeded_db()
    run_video(db, video_provider=FakeVideoProvider(video=good_video(duration_seconds=4.0)))
    assert all(a["duration_seconds"] == 4.0 for a in db.tables["media_assets"])


def test_images_never_get_a_duration():
    db = seeded_db()
    run_media_generation(
        PRODUCTION_ID, repository=MediaRepository(db), image_provider=FakeImageProvider(), storage=FakeStorage(),
    )
    assert all(a["duration_seconds"] is None for a in db.tables["media_assets"])


# --- partial success / idempotency (mirrors the image-path guarantees exactly) ---------------------


def test_one_video_scene_failing_does_not_discard_the_others():
    video_provider = FakeVideoProvider(side_effects=[
        good_video(), MediaProviderAPIError("HTTP 500: rate limited", status=500),
    ])
    db = seeded_db()
    outcome, lines = run_video(db, video_provider=video_provider)
    assert outcome.status == "partial"
    assert outcome.completed_scenes == (1,) and outcome.failed_scenes == {2: "HTTP 500: rate limited"}
    [bad] = [a for a in db.tables["media_assets"] if a["scene_number"] == 2]
    assert bad["status"] == "failed" and bad["storage_path"] is None and bad["error_message"]
    assert any("failed" in line for line in lines)


def test_a_rerun_skips_already_completed_video_scenes_and_never_recalls_the_provider():
    db = seeded_db()
    run_video(db)  # both scenes complete
    video_provider = FakeVideoProvider()
    outcome, lines = run_video(db, video_provider=video_provider)
    assert outcome.status == "completed"
    assert outcome.already_completed_scenes == (1, 2) and outcome.completed_scenes == ()
    assert video_provider.calls == []  # no re-call, no re-charge
    assert len(db.tables["media_assets"]) == 2
    assert any("already generated" in line for line in lines)


def test_a_video_rerun_retries_only_the_previously_failed_scene():
    db = seeded_db()
    run_video(db, video_provider=FakeVideoProvider(side_effects=[
        good_video(), MediaProviderAPIError("HTTP 500", status=500),
    ]))
    retry_provider = FakeVideoProvider()
    outcome, _ = run_video(db, video_provider=retry_provider)
    assert outcome.status == "completed"
    assert outcome.completed_scenes == (2,) and outcome.already_completed_scenes == (1,)
    assert len(retry_provider.calls) == 1


# --- prompt content: video prompts never ask for on-screen text -------------------------------------


def test_video_scene_prompts_never_request_on_screen_text_rendering():
    db = seeded_db()
    run_video(db)
    for a in db.tables["media_assets"]:
        assert "On-screen text to render" not in a["prompt"]
        assert "captions are added separately afterwards" in a["prompt"]


# --- CLI -----------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-not-real")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sr-secret-not-real")


def cli_db():
    db = seeded_db()
    db.tables["productions"][0]["id"] = GOOD_PRODUCTION
    return db


def cli_video(monkeypatch, db, video_provider, extra_args=()):
    monkeypatch.setattr(media, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(media, "_resolve_video_provider", lambda settings: video_provider)
    monkeypatch.setattr(media, "SupabaseStorage", lambda *a, **k: FakeStorage())
    return media.main(["--production-id", GOOD_PRODUCTION, "--media-type", "video", *extra_args])


def test_cli_defaults_to_image_media_type(env, monkeypatch, capsys):
    db = cli_db()
    called = {}
    monkeypatch.setattr(media, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(media, "_resolve_image_provider", lambda settings: FakeImageProvider())
    monkeypatch.setattr(media, "SupabaseStorage", lambda *a, **k: FakeStorage())

    original = media.run_media_generation

    def spy(*a, **kw):
        called["media_type"] = kw.get("media_type")
        return original(*a, **kw)

    monkeypatch.setattr(media, "run_media_generation", spy)
    assert media.main(["--production-id", GOOD_PRODUCTION]) == 0
    assert called["media_type"] == AssetType.IMAGE


def test_cli_media_type_video_calls_the_video_provider_not_the_image_one(env, monkeypatch, capsys):
    db = cli_db()
    assert cli_video(monkeypatch, db, FakeVideoProvider()) == 0
    out = capsys.readouterr()
    assert "Media Generation" in out.out and "Media run completed" in out.out
    assert "secret" not in out.out + out.err
    assert all(a["asset_type"] == "video" for a in db.tables["media_assets"])


def test_cli_video_rejects_an_unimplemented_video_provider(env, monkeypatch, capsys):
    monkeypatch.setenv("MEDIA_VIDEO_PROVIDER", "other-vendor")
    assert media.main(["--production-id", GOOD_PRODUCTION, "--media-type", "video"]) == 2
    assert "not implemented" in capsys.readouterr().err


def test_setting_media_video_env_vars_has_no_effect_when_media_type_is_image(env, monkeypatch, capsys):
    # The core "impossible to accidentally spend video credits" guarantee:
    # unrelated MEDIA_VIDEO_* configuration must not change an image run's
    # identity or call a video provider at all.
    monkeypatch.setenv("MEDIA_VIDEO_PROVIDER", "veo")
    monkeypatch.setenv("MEDIA_VIDEO_MODEL", "veo-3.1-fast-generate-preview")
    db = cli_db()
    monkeypatch.setattr(media, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(media, "_resolve_image_provider", lambda settings: FakeImageProvider())
    monkeypatch.setattr(media, "SupabaseStorage", lambda *a, **k: FakeStorage())
    assert media.main(["--production-id", GOOD_PRODUCTION]) == 0
    [r] = db.tables["media_runs"]
    assert r["video_provider"] is None and r["video_model"] is None


def test_cli_video_all_scenes_failing_exits_3(env, monkeypatch, capsys):
    db = cli_db()
    assert cli_video(
        monkeypatch, db, FakeVideoProvider(error=MediaProviderAPIError("HTTP 500", status=500))
    ) == 3
    err = capsys.readouterr().err
    assert "scene 1:" in err and "scene 2:" in err
