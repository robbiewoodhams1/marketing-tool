"""Assembly V2 (research.assembly_v2): synchronise each scene's video
against its own voiceover, then concatenate. Mirrors test_assembly_service.py
(V1)'s structure; uses FakeAssemblerV2 (never a real ffmpeg process - that is
covered separately, for real, in test_ffmpeg_engine.py) and the same fake
Supabase database/storage doubles as V1."""

import copy

import pytest
from assembly_fixtures import (
    FakeAssemblerV2,
    FakeAssemblyStorage,
    completed_video_asset_row,
    completed_voice_asset_row,
    seeded_assembly_db_with_voice,
)
from media_fixtures import PRODUCTION_ID

from research import assembly_v2
from research.assembly_schema import ASSEMBLY_AUDIO_POLICY_MUTE, ASSEMBLY_AUDIO_POLICY_VOICEOVER
from research.assembly_store import AssemblyRepository
from research.assembly_v2 import AssemblySourceError, run_assembly_v2
from research.ffmpeg_engine import AssembledOutputV2, FFmpegProcessError, SyncedSceneResult
from research.media_storage import StorageError


def run(db, assembler=None, storage=None, captions=False):
    lines = []
    outcome = run_assembly_v2(
        PRODUCTION_ID, repository=AssemblyRepository(db), storage=storage or FakeAssemblyStorage(),
        assembler=assembler or FakeAssemblerV2(), progress=lines.append, captions=captions,
    )
    return outcome, lines


# --- 1. all 6 scenes with video + voice ---------------------------------------------------------------


def test_all_six_scenes_with_video_and_voice_are_assembled_in_order():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2, 3, 4, 5, 6))
    storage = FakeAssemblyStorage()
    outcome, lines = run(db, storage=storage)

    assert outcome.status == "completed"
    assert outcome.source_scene_count == 6

    expected_downloads = []
    for n in (1, 2, 3, 4, 5, 6):
        expected_downloads.append(f"{PRODUCTION_ID}/mrun-video-1/scene-{n}-abcd1234.mp4")
        expected_downloads.append(f"{PRODUCTION_ID}/mrun-voice-1/scene-{n}-ffff0000.wav")
    assert storage.downloads == expected_downloads
    assert any("Assembly completed" in line for line in lines)


def test_source_clips_are_downloaded_and_assembled_in_scene_order_even_if_seeded_out_of_order():
    db = seeded_assembly_db_with_voice(scene_numbers=(3, 1, 2))
    storage = FakeAssemblyStorage()
    assembler = FakeAssemblerV2()
    run(db, assembler=assembler, storage=storage)
    [scenes] = assembler.calls
    assert [s.scene_number for s in scenes] == [1, 2, 3]


# --- 2. missing video ----------------------------------------------------------------------------------


def test_a_missing_video_is_refused_before_any_work():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    db.tables["media_assets"] = [
        a for a in db.tables["media_assets"] if not (a["scene_number"] == 2 and a["asset_type"] == "video")
    ]
    storage = FakeAssemblyStorage()
    assembler = FakeAssemblerV2()
    with pytest.raises(AssemblySourceError, match=r"missing video: \[2\]"):
        run(db, assembler=assembler, storage=storage)
    assert storage.downloads == [] and assembler.calls == []
    assert db.tables["media_assemblies"] == []


# --- 3. missing voice ----------------------------------------------------------------------------------


def test_a_missing_voice_is_refused_before_any_work():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2), voice_scene_numbers=(1,))
    storage = FakeAssemblyStorage()
    assembler = FakeAssemblerV2()
    with pytest.raises(AssemblySourceError, match=r"missing voiceover: \[2\]"):
        run(db, assembler=assembler, storage=storage)
    assert storage.downloads == [] and assembler.calls == []


def test_missing_video_and_missing_voice_on_different_scenes_are_both_reported():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2, 3), voice_scene_numbers=(1, 3))
    db.tables["media_assets"] = [
        a for a in db.tables["media_assets"] if not (a["scene_number"] == 3 and a["asset_type"] == "video")
    ]
    with pytest.raises(AssemblySourceError) as info:
        run(db)
    assert "missing video: [3]" in str(info.value)
    assert "missing voiceover: [2]" in str(info.value)


def test_an_unknown_production_is_refused():
    db = seeded_assembly_db_with_voice(scene_numbers=(1,))
    with pytest.raises(AssemblySourceError, match="does not exist"):
        run_assembly_v2(
            "nope", repository=AssemblyRepository(db), storage=FakeAssemblyStorage(), assembler=FakeAssemblerV2(),
        )


# --- 4. video shorter than voice / 5. voice shorter than video -----------------------------------------


def test_video_shorter_than_voice_is_reported_in_the_scene_manifest_with_the_extend_strategy():
    db = seeded_assembly_db_with_voice(scene_numbers=(1,))
    assembler = FakeAssemblerV2(scene_durations={1: (4.0, 8.0)})  # video shorter
    outcome, _ = run(db, assembler=assembler)
    [entry] = outcome.scene_manifest
    assert entry["video_duration_seconds"] == 4.0 and entry["voice_duration_seconds"] == 8.0
    assert entry["scene_duration_seconds"] == 8.0
    assert entry["sync_strategy"] == "video_extended_frozen_frame"


def test_voice_shorter_than_video_is_reported_in_the_scene_manifest_with_the_pad_strategy():
    db = seeded_assembly_db_with_voice(scene_numbers=(1,))
    assembler = FakeAssemblerV2(scene_durations={1: (8.0, 4.0)})  # voice shorter
    outcome, _ = run(db, assembler=assembler)
    [entry] = outcome.scene_manifest
    assert entry["video_duration_seconds"] == 8.0 and entry["voice_duration_seconds"] == 4.0
    assert entry["scene_duration_seconds"] == 8.0
    assert entry["sync_strategy"] == "audio_padded_with_silence"


# --- 6. source Veo audio is muted / 7. voice is present in final output --------------------------------
# (structurally enforced and covered exhaustively in test_ffmpeg_engine.py's
#  build_scene_sync_filter_complex tests - `[0:a]` never appears, `[1:a]`
#  always does; this service module has no opinion on ffmpeg internals, so it
#  is not re-tested here.)


# --- 8. scene ordering is correct ------------------------------------------------------------------------


def test_scene_manifest_preserves_scene_order_even_when_seeded_out_of_order():
    db = seeded_assembly_db_with_voice(scene_numbers=(3, 1, 2))
    outcome, _ = run(db)
    assert [e["scene_number"] for e in outcome.scene_manifest] == [1, 2, 3]


# --- 9. actual final duration is recorded -----------------------------------------------------------------


def test_the_actual_measured_output_duration_is_recorded_not_the_sum_of_requested_scene_durations():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    output = AssembledOutputV2(
        data=b"final-mp4", mime_type="video/mp4", output_duration_seconds=13.7,
        scene_results=(
            SyncedSceneResult(1, 6.0, 6.0, 6.0, "equal_no_adjustment"),
            SyncedSceneResult(2, 6.0, 7.7, 7.7, "video_extended_frozen_frame"),
        ),
        metadata={},
    )

    class FixedAssembler:
        def assemble_with_voiceover(self, scenes):
            return output

    outcome, _ = run(db, assembler=FixedAssembler())
    assert outcome.output_duration_seconds == 13.7
    [row] = db.tables["media_assemblies"]
    assert row["output_duration_seconds"] == 13.7


# --- 10. changing a voice asset changes assembly run key / 11. changing a video asset changes it too ------


def test_regenerating_one_scenes_voice_produces_a_genuinely_new_assembly():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    first, _ = run(db)
    assert first.status == "completed"

    db.tables["media_assets"].append(
        completed_voice_asset_row(2, asset_id="aasset-2-new", created_at="2026-02-01T00:00:00+00:00")
    )
    second, _ = run(db)

    assert second.status == "completed" and second.assembly_id != first.assembly_id
    assert len(db.tables["media_assemblies"]) == 2


def test_regenerating_one_scenes_video_produces_a_genuinely_new_assembly():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    first, _ = run(db)

    db.tables["media_assets"].append(
        completed_video_asset_row(2, asset_id="vasset-2-new", created_at="2026-02-01T00:00:00+00:00")
    )
    second, _ = run(db)

    assert second.status == "completed" and second.assembly_id != first.assembly_id
    assert len(db.tables["media_assemblies"]) == 2


def test_run_key_uses_the_voiceover_audio_policy_and_carries_source_ids():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    run(db)
    [row] = db.tables["media_assemblies"]
    assert row["audio_policy"] == ASSEMBLY_AUDIO_POLICY_VOICEOVER
    assert row["source_asset_ids"] == ["vasset-1", "vasset-2"]
    assert row["source_voice_asset_ids"] == ["aasset-1", "aasset-2"]


def test_a_v1_style_mute_assembly_of_the_same_video_sources_is_a_different_run_key():
    # V1 and V2 must never collide, even for the exact same video sources -
    # they are different audio policies.
    from research.assembly import run_assembly as run_assembly_v1

    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    v2, _ = run(db)

    from research.ffmpeg_engine import AssembledOutput

    class FakeV1Assembler:
        def assemble(self, clips):
            return AssembledOutput(data=b"v1-mp4", mime_type="video/mp4", output_duration_seconds=12.0, metadata={})

    v1_outcome = run_assembly_v1(
        PRODUCTION_ID, repository=AssemblyRepository(db), storage=FakeAssemblyStorage(), assembler=FakeV1Assembler(),
    )
    assert v1_outcome.run_key != v2.run_key
    assert len(db.tables["media_assemblies"]) == 2
    policies = {row["audio_policy"] for row in db.tables["media_assemblies"]}
    assert policies == {ASSEMBLY_AUDIO_POLICY_MUTE, ASSEMBLY_AUDIO_POLICY_VOICEOVER}


# --- 12. same video + voice sources reuse a completed assembly --------------------------------------------


def test_assembling_the_same_scenes_twice_reuses_the_existing_completed_assembly():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2, 3))
    first, _ = run(db)
    assert first.status == "completed"
    assert len(db.tables["media_assemblies"]) == 1

    assembler = FakeAssemblerV2()
    storage = FakeAssemblyStorage()
    second, lines = run(db, assembler=assembler, storage=storage)

    assert second.status == "exists" and second.assembly_id == first.assembly_id
    assert len(db.tables["media_assemblies"]) == 1
    assert assembler.calls == [] and storage.downloads == []
    assert any("already exists" in line for line in lines)
    assert second.scene_manifest == first.scene_manifest


def test_the_production_and_source_assets_are_never_modified():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    before_prod = copy.deepcopy(db.tables["productions"])
    before_assets = copy.deepcopy(db.tables["media_assets"])
    run(db)
    assert db.tables["productions"] == before_prod
    assert db.tables["media_assets"] == before_assets


# --- 13. failed assembly persists useful error information -------------------------------------------------


def test_ffmpeg_failure_is_recorded_as_a_failed_assembly_not_a_crash():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    error = FFmpegProcessError("ffmpeg exited with status 1 syncing scene 2: boom", returncode=1, stderr_tail="boom")
    outcome, lines = run(db, assembler=FakeAssemblerV2(error=error))

    assert outcome.status == "failed"
    assert "FFmpegProcessError" in outcome.error_message and "scene 2" in outcome.error_message
    [row] = db.tables["media_assemblies"]
    assert row["status"] == "failed" and row["output_storage_path"] is None
    assert row["source_voice_asset_ids"] == ["aasset-1", "aasset-2"]  # kept, for a meaningful retry/audit trail
    assert row["audio_policy"] == ASSEMBLY_AUDIO_POLICY_VOICEOVER
    assert any("failed" in line for line in lines)


def test_source_download_failure_is_recorded_as_a_failed_assembly():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    storage = FakeAssemblyStorage(download_error=StorageError("Supabase Storage download failed: HTTP 404"))
    outcome, _ = run(db, storage=storage)

    assert outcome.status == "failed"
    assert "StorageError" in outcome.error_message
    [row] = db.tables["media_assemblies"]
    assert row["status"] == "failed" and row["error_message"]


def test_output_upload_failure_is_recorded_as_a_failed_assembly():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    storage = FakeAssemblyStorage(upload_error=StorageError("Supabase Storage upload failed: HTTP 500"))
    outcome, _ = run(db, storage=storage)

    assert outcome.status == "failed"
    [row] = db.tables["media_assemblies"]
    assert row["status"] == "failed" and row["error_message"]


def test_a_failed_assembly_can_still_be_retried_and_the_old_failed_row_is_kept():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    storage = FakeAssemblyStorage(upload_error=StorageError("boom"))
    first, _ = run(db, storage=storage)
    assert first.status == "failed"

    second, _ = run(db)  # a working storage this time
    assert second.status == "completed"
    assert len(db.tables["media_assemblies"]) == 2


# --- 14. existing Assembly V1 behaviour remains compatible ---------------------------------------------------


def test_v1_assembly_still_works_unaffected_by_v2s_existence():
    from research.assembly import run_assembly as run_assembly_v1
    from research.ffmpeg_engine import AssembledOutput

    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))  # has voice too, but V1 never looks at it

    class FakeV1Assembler:
        def __init__(self):
            self.calls = []

        def assemble(self, clips):
            self.calls.append(list(clips))
            return AssembledOutput(data=b"v1-mp4", mime_type="video/mp4", output_duration_seconds=12.0, metadata={})

    assembler = FakeV1Assembler()
    outcome = run_assembly_v1(
        PRODUCTION_ID, repository=AssemblyRepository(db), storage=FakeAssemblyStorage(), assembler=assembler,
    )
    assert outcome.status == "completed"
    [row] = db.tables["media_assemblies"]
    assert row["audio_policy"] == ASSEMBLY_AUDIO_POLICY_MUTE
    assert row["source_voice_asset_ids"] == []


# --- Captions V1 ------------------------------------------------------------------------------------------


def test_captions_disabled_by_default_produces_no_caption_data():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    outcome, _ = run(db)
    assert outcome.caption_manifest is None
    [row] = db.tables["media_assemblies"]
    assert row["caption_policy"] is None and row["caption_manifest"] is None


def test_captions_disabled_produces_the_exact_same_run_key_as_before_captions_existed():
    # The idempotency guarantee this task requires: an assembly run WITHOUT
    # requesting captions must hash identically to how research.assembly_v2
    # already computed run keys before Captions V1 was added, so every
    # already-completed V2 assembly remains valid/reusable.
    from research.assembly_schema import ASSEMBLY_AUDIO_POLICY_VOICEOVER
    from research.assembly_store import compute_assembly_run_key

    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    outcome, _ = run(db)
    pre_captions_key = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=("vasset-1", "vasset-2"),
        source_voice_asset_ids=("aasset-1", "aasset-2"), audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    )
    assert outcome.run_key == pre_captions_key


def test_captions_enabled_persists_policy_and_a_manifest_matching_the_actual_scene_durations():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2, 3))
    assembler = FakeAssemblerV2(scene_durations={1: (4.0, 4.0), 2: (6.0, 6.0), 3: (6.0, 6.0)})
    outcome, _ = run(db, assembler=assembler, captions=True)

    assert outcome.status == "completed"
    assert outcome.caption_manifest is not None
    [row] = db.tables["media_assemblies"]
    assert row["caption_policy"] == "captions-burned-v1"
    assert row["caption_manifest"] == list(outcome.caption_manifest)

    # The documented multi-scene offset example: 4s, 6s, 6s -> 0-4 / 4-10 / 10-16.
    by_scene = {}
    for entry in row["caption_manifest"]:
        by_scene.setdefault(entry["scene_number"], []).append(entry)
    assert by_scene[1][0]["start_seconds"] == 0.0
    assert by_scene[1][-1]["end_seconds"] == 4.0
    assert by_scene[2][0]["start_seconds"] == 4.0
    assert by_scene[2][-1]["end_seconds"] == 10.0
    assert by_scene[3][0]["start_seconds"] == 10.0
    assert by_scene[3][-1]["end_seconds"] == 16.0


def test_captions_use_each_scenes_own_exact_voiceover_text_not_a_rewrite():
    db = seeded_assembly_db_with_voice(scene_numbers=(1,))
    assembler = FakeAssemblerV2(scene_durations={1: (6.0, 6.0)})
    outcome, _ = run(db, assembler=assembler, captions=True)
    texts = [e["text"] for e in outcome.caption_manifest]
    assert " ".join(texts) == "Scene 1 voiceover line."  # exactly scene_row(1)'s own voiceover text


def test_captions_burn_call_receives_the_assembled_outputs_own_resolution():
    db = seeded_assembly_db_with_voice(scene_numbers=(1,))
    assembler = FakeAssemblerV2(scene_durations={1: (6.0, 6.0)})
    run(db, assembler=assembler, captions=True)
    [(video_bytes, cues, width, height)] = assembler.burn_calls
    assert video_bytes == b"fake-synced-mp4-bytes"  # the pre-caption assembled output, not the raw source clips
    assert width == 1080 and height == 1920
    assert [(c.start_seconds, c.end_seconds) for c in cues] == [(0.0, 6.0)]


def test_captions_final_duration_is_the_captioned_outputs_own_measured_duration():
    db = seeded_assembly_db_with_voice(scene_numbers=(1,))
    from research.ffmpeg_engine import AssembledOutput

    assembler = FakeAssemblerV2(
        scene_durations={1: (6.0, 6.0)},
        burned_output=AssembledOutput(
            data=b"captioned-bytes", mime_type="video/mp4", output_duration_seconds=6.05, metadata={},
        ),
    )
    outcome, _ = run(db, assembler=assembler, captions=True)
    assert outcome.output_duration_seconds == pytest.approx(6.05)
    [row] = db.tables["media_assemblies"]
    assert row["output_duration_seconds"] == pytest.approx(6.05)


def test_toggling_captions_produces_a_different_run_key_and_a_new_assembly():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    without_captions, _ = run(db)
    with_captions, _ = run(db, captions=True)
    assert without_captions.run_key != with_captions.run_key
    assert without_captions.assembly_id != with_captions.assembly_id
    assert len(db.tables["media_assemblies"]) == 2


def test_running_with_captions_twice_reuses_the_existing_completed_captioned_assembly():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    first, _ = run(db, captions=True)
    assert first.status == "completed"

    assembler = FakeAssemblerV2()
    second, lines = run(db, assembler=assembler, captions=True)
    assert second.status == "exists" and second.assembly_id == first.assembly_id
    assert second.caption_manifest == first.caption_manifest
    assert assembler.calls == [] and assembler.burn_calls == []
    assert len(db.tables["media_assemblies"]) == 1
    assert any("already exists" in line for line in lines)


def test_missing_voice_with_captions_requested_is_still_refused_before_any_work():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2), voice_scene_numbers=(1,))
    assembler = FakeAssemblerV2()
    with pytest.raises(AssemblySourceError, match=r"missing voiceover: \[2\]"):
        run(db, assembler=assembler, captions=True)
    assert assembler.calls == [] and assembler.burn_calls == []


def test_malformed_blank_scene_voiceover_is_recorded_as_a_failed_assembly_not_a_crash():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    # Bypass Production's own validation directly, simulating malformed
    # caption input data reaching Assembly.
    db.tables["productions"][0]["scenes"][1]["voiceover"] = "   "
    assembler = FakeAssemblerV2(scene_durations={1: (6.0, 6.0), 2: (6.0, 6.0)})
    outcome, lines = run(db, assembler=assembler, captions=True)

    assert outcome.status == "failed"
    assert "CaptionError" in outcome.error_message
    [row] = db.tables["media_assemblies"]
    assert row["status"] == "failed" and row["error_message"]
    # A caption failure still records which policy/sources were attempted.
    assert row["caption_policy"] == "captions-burned-v1"
    assert any("failed" in line for line in lines)


def test_a_captioned_run_can_be_retried_after_fixing_malformed_data():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    db.tables["productions"][0]["scenes"][1]["voiceover"] = "   "
    first, _ = run(db, assembler=FakeAssemblerV2(scene_durations={1: (6.0, 6.0), 2: (6.0, 6.0)}), captions=True)
    assert first.status == "failed"

    db.tables["productions"][0]["scenes"][1]["voiceover"] = "Scene 2 voiceover line."
    second, _ = run(db, assembler=FakeAssemblerV2(scene_durations={1: (6.0, 6.0), 2: (6.0, 6.0)}), captions=True)
    assert second.status == "completed"
    assert len(db.tables["media_assemblies"]) == 2


def test_existing_v2_behaviour_without_captions_remains_fully_intact():
    # Captions must be strictly additive: the whole no-captions test suite
    # above this section already exercises this, but assert the shape once
    # more explicitly for clarity.
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2, 3, 4, 5, 6))
    outcome, _ = run(db, captions=False)
    assert outcome.status == "completed"
    assert outcome.caption_manifest is None
    [row] = db.tables["media_assemblies"]
    assert row["caption_policy"] is None and row["caption_manifest"] is None
    assert row["audio_policy"] == ASSEMBLY_AUDIO_POLICY_VOICEOVER


# --- CLI ------------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sr-secret-not-real")


def cli(monkeypatch, db, assembler=None, storage=None, extra_args=()):
    monkeypatch.setattr(assembly_v2, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(assembly_v2, "SupabaseStorage", lambda *a, **k: storage or FakeAssemblyStorage())
    monkeypatch.setattr(
        assembly_v2.FFmpegAssembler, "from_settings", staticmethod(lambda settings: assembler or FakeAssemblerV2())
    )
    return assembly_v2.main(["--production-id", PRODUCTION_ID, *extra_args])


def test_cli_missing_supabase_config_exits_2(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert assembly_v2.main(["--production-id", PRODUCTION_ID]) == 2


def test_cli_missing_ffmpeg_exits_2_and_names_the_missing_binary(env, monkeypatch, capsys):
    monkeypatch.setattr(assembly_v2.shutil, "which", lambda name: None)
    assert assembly_v2.main(["--production-id", PRODUCTION_ID]) == 2
    err = capsys.readouterr().err
    assert "ffmpeg" in err and "not found on PATH" in err


def test_cli_success_prints_progress_and_no_secrets(env, monkeypatch, capsys):
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    assert cli(monkeypatch, db) == 0
    out = capsys.readouterr().out
    assert "Media Assembly V2" in out and "Assembly completed" in out
    assert "secret" not in out + capsys.readouterr().err


def test_cli_missing_voice_exits_1(env, monkeypatch, capsys):
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2), voice_scene_numbers=(1,))
    assert cli(monkeypatch, db) == 1
    assert "Cannot assemble" in capsys.readouterr().err


def test_cli_ffmpeg_failure_exits_3(env, monkeypatch, capsys):
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    error = FFmpegProcessError("boom", returncode=1, stderr_tail="boom")
    assert cli(monkeypatch, db, assembler=FakeAssemblerV2(error=error)) == 3
    assert "Assembly failed" in capsys.readouterr().err


def test_cli_reused_assembly_still_exits_0(env, monkeypatch, capsys):
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    assert cli(monkeypatch, db) == 0
    assert cli(monkeypatch, db) == 0
    out = capsys.readouterr().out
    assert "found (identical assembly already existed)" in out


def test_cli_captions_flag_enables_captions(env, monkeypatch, capsys):
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    assert cli(monkeypatch, db, extra_args=["--captions"]) == 0
    out = capsys.readouterr().out
    assert "captions" in out.lower()
    [row] = db.tables["media_assemblies"]
    assert row["caption_policy"] == "captions-burned-v1"


def test_cli_without_captions_flag_leaves_captions_disabled(env, monkeypatch, capsys):
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    assert cli(monkeypatch, db) == 0
    [row] = db.tables["media_assemblies"]
    assert row["caption_policy"] is None
