"""Voice Generation V1: persistence, narration passthrough, idempotency,
partial success, and the CLI - through `run_voice_generation` end to end."""

from __future__ import annotations

import copy

import pytest
from media_fixtures import (
    PRODUCTION_ID,
    FakeStorage,
    FakeVoiceProvider,
    good_audio,
    production_row,
    scene_row,
    seeded_db,
)

from research import voice
from research.media_providers import MediaProviderAPIError
from research.media_schema import AssetType
from research.media_store import MediaRepository, compute_run_key
from research.voice import VoiceSourceError, run_voice_generation

GOOD_PRODUCTION = "8e610e89-87e7-4838-a7f7-222d7fceda0b"


def run(db, voice_provider=None, storage=None, **kw):
    lines = []
    outcome = run_voice_generation(
        PRODUCTION_ID, repository=MediaRepository(db), voice_provider=voice_provider or FakeVoiceProvider(),
        storage=storage or FakeStorage(), progress=lines.append, **kw,
    )
    return outcome, lines


# --- run identity: a voice run never leaks image/video config, and vice versa ---------------------


def test_a_voice_run_records_only_the_voice_provider_never_image_or_video():
    db = seeded_db()
    run(db, voice_provider=FakeVoiceProvider(model="gemini-2.5-flash-preview-tts"))
    [r] = db.tables["media_runs"]
    assert r["voice_provider"] == "gemini" and r["voice_model"] == "gemini-2.5-flash-preview-tts"
    assert r["image_provider"] is None and r["image_model"] is None
    assert r["video_provider"] is None and r["video_model"] is None


def test_a_voice_run_key_differs_from_an_image_or_video_run_key_for_the_same_production():
    voice_key = compute_run_key(
        production_id=PRODUCTION_ID, image_provider=None, image_model=None, video_provider=None,
        video_model=None, voice_provider="gemini", voice_model="m", pack_version="voice-v1",
    )
    image_key = compute_run_key(
        production_id=PRODUCTION_ID, image_provider="gemini", image_model="m", video_provider=None,
        video_model=None, pack_version="media-pack-v1",
    )
    assert voice_key != image_key


# --- narration passthrough: production's own words, unchanged -------------------------------------


def test_each_scenes_own_voiceover_text_is_passed_to_the_provider_unchanged():
    db = seeded_db()
    voice_provider = FakeVoiceProvider()
    run(db, voice_provider=voice_provider)
    texts = [call[0] for call in voice_provider.calls]
    assert texts == ["Scene 1 voiceover line.", "Scene 2 voiceover line."]


def test_the_persisted_prompt_is_exactly_the_scenes_voiceover_never_a_built_prompt():
    db = seeded_db()
    run(db)
    by_scene = {a["scene_number"]: a for a in db.tables["media_assets"]}
    assert by_scene[1]["prompt"] == "Scene 1 voiceover line."
    assert by_scene[2]["prompt"] == "Scene 2 voiceover line."


def test_voice_and_language_options_are_forwarded_to_the_provider():
    db = seeded_db()
    voice_provider = FakeVoiceProvider()
    run(db, voice_provider=voice_provider, voice="Puck", language="fr-FR")
    assert all(call[1] == "Puck" and call[2] == "fr-FR" for call in voice_provider.calls)


def test_the_production_is_never_modified():
    db = seeded_db()
    before = copy.deepcopy(db.tables["productions"])
    run(db)
    assert db.tables["productions"] == before
    assert not any(c[0] == "update" for c in db.calls)


# --- missing narration is refused before any work --------------------------------------------------


def test_a_scene_with_no_voiceover_text_is_refused_before_any_work():
    db = seeded_db(scenes=[scene_row(1, voiceover=""), scene_row(2)])
    voice_provider = FakeVoiceProvider()
    with pytest.raises(VoiceSourceError, match=r"scene\(s\) \[1\]"):
        run(db, voice_provider=voice_provider)
    assert voice_provider.calls == [] and db.tables["media_assets"] == []


def test_a_scene_with_only_whitespace_voiceover_counts_as_no_narration():
    db = seeded_db(scenes=[scene_row(1, voiceover="   \n  ")])
    with pytest.raises(VoiceSourceError, match="no voiceover text"):
        run(db)


def test_an_unknown_requested_scene_is_refused_before_any_work():
    db = seeded_db()
    voice_provider = FakeVoiceProvider()
    with pytest.raises(VoiceSourceError, match=r"scene\(s\) \[99\]"):
        run(db, voice_provider=voice_provider, scenes=[99])
    assert voice_provider.calls == []


def test_production_not_found_is_refused_before_any_work():
    db = seeded_db()
    with pytest.raises(VoiceSourceError, match="does not exist"):
        run_voice_generation(
            "nope", repository=MediaRepository(db), voice_provider=FakeVoiceProvider(), storage=FakeStorage(),
        )


# --- the happy path -------------------------------------------------------------------------------


def test_a_complete_voice_run_generates_and_persists_every_scene():
    db = seeded_db()
    storage = FakeStorage()
    outcome, lines = run(db, storage=storage)

    assert outcome.status == "completed"
    assert outcome.completed_scenes == (1, 2)
    assets = db.tables["media_assets"]
    assert len(assets) == 2
    for a in assets:
        assert a["asset_type"] == "audio" and a["audio_subtype"] == "voiceover"
        assert a["status"] == "completed" and a["mime_type"] == "audio/wav"
        assert a["duration_seconds"] == 6.0
    assert len(storage.uploads) == 2
    assert all(path.endswith(".wav") for path, _, _ in storage.uploads)
    assert any("voiceover generated" in line for line in lines)


def test_a_single_requested_scene_generates_only_that_one():
    db = seeded_db()
    outcome, _ = run(db, scenes=[1])
    assert outcome.completed_scenes == (1,) and len(db.tables["media_assets"]) == 1


def test_cost_is_persisted_from_the_providers_own_reported_figure():
    db = seeded_db()
    run(db, voice_provider=FakeVoiceProvider(audio=good_audio(cost_amount=0.00251, cost_currency="USD")))
    assert all(a["cost_amount"] == 0.00251 and a["cost_currency"] == "USD" for a in db.tables["media_assets"])


def test_images_and_videos_never_get_an_audio_subtype():
    from research.media import run_media_generation
    from media_fixtures import FakeImageProvider

    db = seeded_db()
    run_media_generation(PRODUCTION_ID, repository=MediaRepository(db), image_provider=FakeImageProvider(), storage=FakeStorage())
    assert all(a["audio_subtype"] is None for a in db.tables["media_assets"])


# --- partial success / idempotency (mirrors the image/video-path guarantees exactly) ---------------


def test_one_scenes_narration_failing_does_not_discard_the_others():
    voice_provider = FakeVoiceProvider(side_effects=[
        good_audio(), MediaProviderAPIError("HTTP 500: rate limited", status=500),
    ])
    db = seeded_db()
    outcome, lines = run(db, voice_provider=voice_provider)
    assert outcome.status == "partial"
    assert outcome.completed_scenes == (1,) and outcome.failed_scenes == {2: "HTTP 500: rate limited"}
    [bad] = [a for a in db.tables["media_assets"] if a["scene_number"] == 2]
    assert bad["status"] == "failed" and bad["storage_path"] is None and bad["error_message"]
    assert any("failed" in line for line in lines)


def test_a_rerun_skips_already_narrated_scenes_and_never_recalls_the_provider():
    db = seeded_db()
    run(db)
    voice_provider = FakeVoiceProvider()
    outcome, lines = run(db, voice_provider=voice_provider)
    assert outcome.status == "completed"
    assert outcome.already_completed_scenes == (1, 2) and outcome.completed_scenes == ()
    assert voice_provider.calls == []
    assert len(db.tables["media_assets"]) == 2
    assert any("already narrated" in line for line in lines)


def test_a_rerun_retries_only_the_previously_failed_scene():
    db = seeded_db()
    run(db, voice_provider=FakeVoiceProvider(side_effects=[
        good_audio(), MediaProviderAPIError("HTTP 500", status=500),
    ]))
    retry_provider = FakeVoiceProvider()
    outcome, _ = run(db, voice_provider=retry_provider)
    assert outcome.status == "completed"
    assert outcome.completed_scenes == (2,) and outcome.already_completed_scenes == (1,)
    assert len(retry_provider.calls) == 1


# --- CLI --------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-not-real")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sr-secret-not-real")


def cli_db():
    db = seeded_db()
    db.tables["productions"][0]["id"] = GOOD_PRODUCTION
    return db


def cli(monkeypatch, db, voice_provider, extra_args=()):
    monkeypatch.setattr(voice, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(voice, "_resolve_voice_provider", lambda settings: voice_provider)
    monkeypatch.setattr(voice, "SupabaseStorage", lambda *a, **k: FakeStorage())
    return voice.main(["--production-id", GOOD_PRODUCTION, *extra_args])


def test_cli_missing_configuration_exits_2_without_doing_anything(env, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY")
    called = []
    monkeypatch.setattr(voice, "run_voice_generation", lambda *a, **k: called.append(1))
    assert voice.main(["--production-id", "8e610e89-87e7-4838-a7f7-222d7fceda0b"]) == 2
    out = capsys.readouterr()
    assert called == [] and "secret" not in out.out + out.err


def test_cli_missing_supabase_config_exits_2(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert voice.main(["--production-id", "8e610e89-87e7-4838-a7f7-222d7fceda0b"]) == 2


def test_cli_rejects_an_unimplemented_voice_provider(env, monkeypatch):
    monkeypatch.setenv("MEDIA_VOICE_PROVIDER", "elevenlabs")
    assert voice.main(["--production-id", "8e610e89-87e7-4838-a7f7-222d7fceda0b"]) == 2


def test_cli_success_prints_progress_and_no_secrets(env, monkeypatch, capsys):
    db = cli_db()
    assert cli(monkeypatch, db, FakeVoiceProvider()) == 0
    out = capsys.readouterr()
    assert "Voice Generation" in out.out and "Voice run completed" in out.out
    assert "secret" not in out.out + out.err
    assert len(db.tables["media_assets"]) == 2


def test_cli_respects_the_scene_flag(env, monkeypatch):
    db = cli_db()
    assert cli(monkeypatch, db, FakeVoiceProvider(), extra_args=["--scene", "1"]) == 0
    assert len(db.tables["media_assets"]) == 1


def test_cli_respects_the_voice_flag(env, monkeypatch):
    db = cli_db()
    voice_provider = FakeVoiceProvider()
    cli(monkeypatch, db, voice_provider, extra_args=["--voice", "Puck"])
    assert all(call[1] == "Puck" for call in voice_provider.calls)


def test_cli_missing_narration_exits_1(env, monkeypatch, capsys):
    db = cli_db()
    db.tables["productions"][0]["scenes"] = [scene_row(1, voiceover="")]
    assert cli(monkeypatch, db, FakeVoiceProvider()) == 1
    assert "Cannot generate voice" in capsys.readouterr().err


def test_cli_all_scenes_failing_exits_3_and_reports_each_failure(env, monkeypatch, capsys):
    db = cli_db()
    assert cli(monkeypatch, db, FakeVoiceProvider(error=MediaProviderAPIError("HTTP 500", status=500))) == 3
    err = capsys.readouterr().err
    assert "scene 1:" in err and "scene 2:" in err


def test_cli_partial_success_still_exits_0(env, monkeypatch, capsys):
    db = cli_db()
    provider = FakeVoiceProvider(side_effects=[good_audio(), MediaProviderAPIError("HTTP 500", status=500)])
    assert cli(monkeypatch, db, provider) == 0
    out = capsys.readouterr()
    assert "partial" in out.out
