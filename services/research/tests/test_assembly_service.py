import copy

import pytest
from assembly_fixtures import FakeAssembler, FakeAssemblyStorage, completed_video_asset_row, seeded_assembly_db
from media_fixtures import PRODUCTION_ID

from research import assembly
from research.assembly import AssemblySourceError, run_assembly
from research.assembly_store import AssemblyRepository
from research.ffmpeg_engine import AssembledOutput, FFmpegProcessError
from research.media_storage import StorageError


def run(db, assembler=None, storage=None):
    lines = []
    outcome = run_assembly(
        PRODUCTION_ID, repository=AssemblyRepository(db), storage=storage or FakeAssemblyStorage(),
        assembler=assembler or FakeAssembler(), progress=lines.append,
    )
    return outcome, lines


# --- scene ordering / source selection ---------------------------------------------------------------


def test_source_clips_are_downloaded_and_assembled_in_scene_order():
    db = seeded_assembly_db(scene_numbers=(3, 1, 2))  # seeded out of order
    storage = FakeAssemblyStorage()
    assembler = FakeAssembler()
    run(db, assembler=assembler, storage=storage)
    assert storage.downloads == [
        f"{PRODUCTION_ID}/mrun-video-1/scene-1-abcd1234.mp4",
        f"{PRODUCTION_ID}/mrun-video-1/scene-2-abcd1234.mp4",
        f"{PRODUCTION_ID}/mrun-video-1/scene-3-abcd1234.mp4",
    ]


# --- missing / incomplete scene detection ------------------------------------------------------------


def test_a_missing_scene_is_refused_before_any_work():
    db = seeded_assembly_db(scene_numbers=(1, 2))
    db.tables["media_assets"] = [a for a in db.tables["media_assets"] if a["scene_number"] != 2]
    storage = FakeAssemblyStorage()
    assembler = FakeAssembler()
    with pytest.raises(AssemblySourceError, match=r"scene\(s\) \[2\]"):
        run(db, assembler=assembler, storage=storage)
    assert storage.downloads == [] and assembler.calls == []
    assert db.tables["media_assemblies"] == []


def test_a_scene_with_only_a_failed_video_attempt_counts_as_missing():
    db = seeded_assembly_db(scene_numbers=(1, 2))
    for asset in db.tables["media_assets"]:
        if asset["scene_number"] == 2:
            asset["status"] = "failed"
            asset["storage_bucket"] = asset["storage_path"] = asset["storage_url"] = None
            asset["error_message"] = "MediaProviderAPIError: HTTP 500"
    with pytest.raises(AssemblySourceError, match=r"scene\(s\) \[2\]"):
        run(db)


def test_an_unknown_production_is_refused():
    db = seeded_assembly_db(scene_numbers=(1,))
    with pytest.raises(AssemblySourceError, match="does not exist"):
        run_assembly(
            "nope", repository=AssemblyRepository(db), storage=FakeAssemblyStorage(), assembler=FakeAssembler(),
        )


# --- source duration handling -------------------------------------------------------------------------


def test_source_duration_is_the_sum_of_each_scenes_actual_generated_duration():
    db = seeded_assembly_db(scene_numbers=(1, 2, 3), duration_seconds=6.0)
    outcome, _ = run(db)
    assert outcome.source_duration_seconds == 18.0


def test_source_duration_is_unknown_not_a_partial_sum_if_any_scene_has_no_known_duration():
    db = seeded_assembly_db(scene_numbers=(1, 2))
    for asset in db.tables["media_assets"]:
        if asset["scene_number"] == 2:
            asset["duration_seconds"] = None
    outcome, _ = run(db)
    assert outcome.source_duration_seconds is None


# --- successful assembly path -----------------------------------------------------------------------


def test_a_successful_assembly_is_persisted_and_reported():
    db = seeded_assembly_db(scene_numbers=(1, 2, 3))
    output = AssembledOutput(data=b"final-mp4", mime_type="video/mp4", output_duration_seconds=17.5, metadata={})
    outcome, lines = run(db, assembler=FakeAssembler(output=output))

    assert outcome.status == "completed"
    assert outcome.source_scene_count == 3
    assert outcome.output_duration_seconds == 17.5
    assert outcome.output_url and outcome.output_url.endswith(".mp4")

    [row] = db.tables["media_assemblies"]
    assert row["status"] == "completed"
    assert row["source_asset_ids"] == ["vasset-1", "vasset-2", "vasset-3"]
    assert row["output_duration_seconds"] == 17.5
    assert any("Assembly completed" in line for line in lines)


def test_the_production_is_never_modified():
    db = seeded_assembly_db(scene_numbers=(1, 2))
    before = copy.deepcopy(db.tables["productions"])
    run(db)
    assert db.tables["productions"] == before
    assert not any(c[0] == "update" for c in db.calls)


def test_source_assets_are_never_modified():
    db = seeded_assembly_db(scene_numbers=(1, 2))
    before = copy.deepcopy(db.tables["media_assets"])
    run(db)
    assert db.tables["media_assets"] == before  # Assembly only ever reads them


# --- idempotency ---------------------------------------------------------------------------------------


def test_assembling_the_same_scenes_twice_reuses_the_existing_completed_assembly():
    db = seeded_assembly_db(scene_numbers=(1, 2, 3))
    first, _ = run(db)
    assert first.status == "completed"
    assert len(db.tables["media_assemblies"]) == 1

    assembler = FakeAssembler()
    storage = FakeAssemblyStorage()
    second, lines = run(db, assembler=assembler, storage=storage)

    assert second.status == "exists" and second.assembly_id == first.assembly_id
    assert len(db.tables["media_assemblies"]) == 1  # nothing duplicated
    assert assembler.calls == [] and storage.downloads == []  # no re-download, no re-encode
    assert any("already exists" in line for line in lines)


def test_regenerating_one_scenes_video_produces_a_genuinely_new_assembly():
    db = seeded_assembly_db(scene_numbers=(1, 2, 3))
    first, _ = run(db)

    # Scene 2 is regenerated (a new completed row, e.g. after a Media
    # Direction edit) - a real new asset id, so a re-assembly must be a new
    # attempt, not silently skipped.
    db.tables["media_assets"].append(
        completed_video_asset_row(2, asset_id="vasset-2-new", created_at="2026-02-01T00:00:00+00:00")
    )
    second, _ = run(db)

    assert second.status == "completed" and second.assembly_id != first.assembly_id
    assert len(db.tables["media_assemblies"]) == 2


# --- failure handling: ffmpeg / download / upload -----------------------------------------------------


def test_ffmpeg_failure_is_recorded_as_a_failed_assembly_not_a_crash():
    db = seeded_assembly_db(scene_numbers=(1, 2))
    error = FFmpegProcessError("ffmpeg exited with status 1: boom", returncode=1, stderr_tail="boom")
    outcome, lines = run(db, assembler=FakeAssembler(error=error))

    assert outcome.status == "failed"
    assert "FFmpegProcessError" in outcome.error_message
    [row] = db.tables["media_assemblies"]
    assert row["status"] == "failed" and row["output_storage_path"] is None
    assert any("failed" in line for line in lines)


def test_source_download_failure_is_recorded_as_a_failed_assembly():
    db = seeded_assembly_db(scene_numbers=(1, 2))
    storage = FakeAssemblyStorage(download_error=StorageError("Supabase Storage download failed: HTTP 404"))
    outcome, _ = run(db, storage=storage)

    assert outcome.status == "failed"
    assert "StorageError" in outcome.error_message
    [row] = db.tables["media_assemblies"]
    assert row["status"] == "failed" and row["error_message"]


def test_output_upload_failure_is_recorded_as_a_failed_assembly():
    db = seeded_assembly_db(scene_numbers=(1, 2))
    storage = FakeAssemblyStorage(upload_error=StorageError("Supabase Storage upload failed: HTTP 500"))
    outcome, _ = run(db, storage=storage)

    assert outcome.status == "failed"
    assert "StorageError" in outcome.error_message
    [row] = db.tables["media_assemblies"]
    assert row["status"] == "failed"
    # ffmpeg DID run (this was an upload failure, not an encode failure).
    assert row["error_message"]


def test_a_failed_assembly_can_still_be_retried_and_the_old_failed_row_is_kept():
    db = seeded_assembly_db(scene_numbers=(1, 2))
    storage = FakeAssemblyStorage(upload_error=StorageError("boom"))
    first, _ = run(db, storage=storage)
    assert first.status == "failed"

    second, _ = run(db)  # a working storage this time
    assert second.status == "completed"
    assert len(db.tables["media_assemblies"]) == 2  # the failed row is kept, a new completed one is added


# --- CLI ------------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sr-secret-not-real")


def cli(monkeypatch, db, assembler=None, storage=None):
    monkeypatch.setattr(assembly, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(assembly, "SupabaseStorage", lambda *a, **k: storage or FakeAssemblyStorage())
    monkeypatch.setattr(assembly.FFmpegAssembler, "from_settings", staticmethod(lambda settings: assembler or FakeAssembler()))
    return assembly.main(["--production-id", PRODUCTION_ID])


def test_cli_missing_supabase_config_exits_2(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert assembly.main(["--production-id", PRODUCTION_ID]) == 2


def test_cli_missing_ffmpeg_exits_2_and_names_the_missing_binary(env, monkeypatch, capsys):
    monkeypatch.setattr(assembly.shutil, "which", lambda name: None)
    assert assembly.main(["--production-id", PRODUCTION_ID]) == 2
    err = capsys.readouterr().err
    assert "ffmpeg" in err and "not found on PATH" in err


def test_cli_success_prints_progress_and_no_secrets(env, monkeypatch, capsys):
    db = seeded_assembly_db(scene_numbers=(1, 2))
    assert cli(monkeypatch, db) == 0
    out = capsys.readouterr().out
    assert "Media Assembly" in out and "Assembly completed" in out
    assert "secret" not in out + capsys.readouterr().err


def test_cli_missing_scene_exits_1(env, monkeypatch, capsys):
    db = seeded_assembly_db(scene_numbers=(1, 2))
    db.tables["media_assets"] = [a for a in db.tables["media_assets"] if a["scene_number"] != 2]
    assert cli(monkeypatch, db) == 1
    assert "Cannot assemble" in capsys.readouterr().err


def test_cli_ffmpeg_failure_exits_3(env, monkeypatch, capsys):
    db = seeded_assembly_db(scene_numbers=(1, 2))
    error = FFmpegProcessError("boom", returncode=1, stderr_tail="boom")
    assert cli(monkeypatch, db, assembler=FakeAssembler(error=error)) == 3
    assert "Assembly failed" in capsys.readouterr().err


def test_cli_reused_assembly_still_exits_0(env, monkeypatch, capsys):
    db = seeded_assembly_db(scene_numbers=(1, 2))
    assert cli(monkeypatch, db) == 0
    assert cli(monkeypatch, db) == 0  # second run: identical assembly already exists
    out = capsys.readouterr().out
    assert "found (identical assembly already existed)" in out
