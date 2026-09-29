import re

from assembly_fixtures import completed_video_asset_row, seeded_assembly_db
from media_fixtures import PRODUCTION_ID

from research.assembly_schema import AssemblyRecord, AssemblyStatus
from research.assembly_store import AssemblyRepository, compute_assembly_run_key

# --- compute_assembly_run_key -----------------------------------------------------------------------


def test_run_key_is_a_sha256_hex_digest_and_stable():
    key = compute_assembly_run_key(production_id=PRODUCTION_ID, source_asset_ids=["a", "b"])
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert key == compute_assembly_run_key(production_id=PRODUCTION_ID, source_asset_ids=["a", "b"])


def test_run_key_is_sensitive_to_asset_order():
    forward = compute_assembly_run_key(production_id=PRODUCTION_ID, source_asset_ids=["a", "b"])
    backward = compute_assembly_run_key(production_id=PRODUCTION_ID, source_asset_ids=["b", "a"])
    assert forward != backward


def test_run_key_is_sensitive_to_asset_identity():
    a = compute_assembly_run_key(production_id=PRODUCTION_ID, source_asset_ids=["a", "b"])
    b = compute_assembly_run_key(production_id=PRODUCTION_ID, source_asset_ids=["a", "c"])
    assert a != b


def test_run_key_is_sensitive_to_the_production():
    a = compute_assembly_run_key(production_id="prod-1", source_asset_ids=["a"])
    b = compute_assembly_run_key(production_id="prod-2", source_asset_ids=["a"])
    assert a != b


# --- get_completed_video_assets ---------------------------------------------------------------------


def test_returns_one_entry_per_scene_in_scene_order():
    db = seeded_assembly_db(scene_numbers=(3, 1, 2))  # seeded out of order
    videos = AssemblyRepository(db).get_completed_video_assets(PRODUCTION_ID)
    assert [v.scene_number for v in videos] == [1, 2, 3]


def test_carries_the_storage_reference_and_duration_of_each_scenes_video():
    db = seeded_assembly_db(scene_numbers=(1,), duration_seconds=8.0)
    [video] = AssemblyRepository(db).get_completed_video_assets(PRODUCTION_ID)
    assert video.storage_bucket == "media-assets"
    assert video.storage_path.endswith(".mp4")
    assert video.duration_seconds == 8.0


def test_a_retried_scene_resolves_to_its_latest_completed_row():
    db = seeded_assembly_db(scene_numbers=(1,))
    # An earlier, now-superseded completed attempt for the same scene (real
    # generation only ever adds rows - see research.media_store).
    db.tables["media_assets"].append(
        completed_video_asset_row(1, asset_id="vasset-1-old", created_at="2025-01-01T00:00:00+00:00")
    )
    videos = AssemblyRepository(db).get_completed_video_assets(PRODUCTION_ID)
    assert len(videos) == 1 and videos[0].id == "vasset-1"  # the later row, not "vasset-1-old"


def test_a_scene_whose_only_completed_video_asset_predates_a_current_failed_retry_still_counts():
    # A failed retry does not erase the earlier success (media_assets is
    # append-only) - the scene still has a usable, completed video.
    db = seeded_assembly_db(scene_numbers=(1,))
    db.tables["media_assets"].append({
        **completed_video_asset_row(1, asset_id="vasset-1-retry", created_at="2026-01-02T00:00:00+00:00"),
        "status": "failed", "storage_bucket": None, "storage_path": None, "storage_url": None,
        "error_message": "MediaProviderTimeoutError: timed out",
    })
    videos = AssemblyRepository(db).get_completed_video_assets(PRODUCTION_ID)
    assert len(videos) == 1 and videos[0].id == "vasset-1"


def test_image_assets_are_never_treated_as_source_video():
    db = seeded_assembly_db(scene_numbers=(1,))
    db.tables["media_assets"].append({
        **completed_video_asset_row(1, asset_id="iasset-1", created_at="2026-01-03T00:00:00+00:00"),
        "asset_type": "image", "mime_type": "image/png",
    })
    videos = AssemblyRepository(db).get_completed_video_assets(PRODUCTION_ID)
    assert len(videos) == 1 and videos[0].id == "vasset-1"


def test_assets_belonging_to_a_different_production_are_excluded():
    db = seeded_assembly_db(scene_numbers=(1,))
    db.tables["media_assets"].append(
        completed_video_asset_row(1, asset_id="other-prod-asset", created_at="2026-01-01T00:00:05+00:00",
                                   production_id="some-other-production")
    )
    videos = AssemblyRepository(db).get_completed_video_assets(PRODUCTION_ID)
    assert len(videos) == 1 and videos[0].id == "vasset-1"


def test_a_scene_with_no_completed_video_at_all_is_simply_absent():
    db = seeded_assembly_db(scene_numbers=(1, 2))
    db.tables["media_assets"] = [a for a in db.tables["media_assets"] if a["scene_number"] != 2]
    videos = AssemblyRepository(db).get_completed_video_assets(PRODUCTION_ID)
    assert [v.scene_number for v in videos] == [1]


# --- save_assembly / find_assembly / get_assembly ----------------------------------------------------


def key():
    return compute_assembly_run_key(production_id=PRODUCTION_ID, source_asset_ids=["vasset-1"])


def test_a_completed_assembly_can_be_saved_and_found_and_read_back():
    db = seeded_assembly_db(scene_numbers=(1,))
    repo = AssemblyRepository(db)
    record = AssemblyRecord(
        status=AssemblyStatus.COMPLETED, run_key=key(), source_asset_ids=("vasset-1",), source_scene_count=1,
        source_duration_seconds=6.0, output_duration_seconds=6.1, output_storage_bucket="media-assets",
        output_storage_path="p/assembly/x.mp4", output_storage_url="https://x/p/assembly/x.mp4",
        output_mime_type="video/mp4",
    )
    assembly_id = repo.save_assembly(production_id=PRODUCTION_ID, record=record)
    assert repo.find_assembly(key()) == assembly_id
    row = repo.get_assembly(assembly_id)
    assert row["status"] == "completed" and row["output_duration_seconds"] == 6.1
    assert row["completed_at"] is not None


def test_a_failed_assembly_can_be_saved_and_read_back_but_never_counts_as_done():
    db = seeded_assembly_db(scene_numbers=(1,))
    repo = AssemblyRepository(db)
    record = AssemblyRecord(
        status=AssemblyStatus.FAILED, run_key=key(), source_asset_ids=("vasset-1",), source_scene_count=1,
        source_duration_seconds=6.0, error_message="FFmpegProcessError: boom",
    )
    assembly_id = repo.save_assembly(production_id=PRODUCTION_ID, record=record)
    row = repo.get_assembly(assembly_id)
    assert row["status"] == "failed" and row["error_message"] == "FFmpegProcessError: boom"
    assert row["completed_at"] is None
    # A failed attempt must remain retryable - it is never "found" as an
    # already-done assembly (see find_assembly's own docstring).
    assert repo.find_assembly(key()) is None


def test_get_assembly_of_an_unknown_id_is_none():
    db = seeded_assembly_db(scene_numbers=(1,))
    assert AssemblyRepository(db).get_assembly("nope") is None


def test_find_assembly_of_an_unknown_key_is_none():
    db = seeded_assembly_db(scene_numbers=(1,))
    assert AssemblyRepository(db).find_assembly("0" * 64) is None
