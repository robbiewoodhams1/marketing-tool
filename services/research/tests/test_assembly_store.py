import re

from assembly_fixtures import (
    completed_video_asset_row,
    completed_voice_asset_row,
    seeded_assembly_db,
    seeded_assembly_db_with_voice,
)
from media_fixtures import PRODUCTION_ID

from research.assembly_schema import ASSEMBLY_AUDIO_POLICY_MUTE, ASSEMBLY_AUDIO_POLICY_VOICEOVER, AssemblyRecord, AssemblyStatus
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


def test_run_key_defaults_to_no_voice_ids_and_the_mute_policy():
    # A V1 (research.assembly) call site never passes voice ids/policy - the
    # defaults must reproduce exactly what it always computed.
    a = compute_assembly_run_key(production_id=PRODUCTION_ID, source_asset_ids=["a", "b"])
    b = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["a", "b"], source_voice_asset_ids=(),
        audio_policy=ASSEMBLY_AUDIO_POLICY_MUTE,
    )
    assert a == b


def test_run_key_is_sensitive_to_voice_asset_identity():
    # Regenerating a scene's VOICEOVER (video unchanged) must produce a
    # genuinely new assembly, never a silently-reused one.
    a = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1", "v2"], source_voice_asset_ids=["a1", "a2"],
        audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    )
    b = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1", "v2"], source_voice_asset_ids=["a1", "a2-new"],
        audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    )
    assert a != b


def test_run_key_is_sensitive_to_voice_asset_order():
    a = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1", "v2"], source_voice_asset_ids=["a1", "a2"],
        audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    )
    b = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1", "v2"], source_voice_asset_ids=["a2", "a1"],
        audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    )
    assert a != b


def test_run_key_is_sensitive_to_video_asset_identity_even_with_voice_present():
    # The symmetric case: video changes, voice does not - still a new key.
    a = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1", "v2"], source_voice_asset_ids=["a1", "a2"],
        audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    )
    b = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1", "v2-new"], source_voice_asset_ids=["a1", "a2"],
        audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    )
    assert a != b


def test_run_key_is_sensitive_to_the_audio_policy():
    # Same source assets, different policy (e.g. a future audio-mixing
    # version) - must never collide with today's V1/V2 keys.
    mute = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1"], audio_policy=ASSEMBLY_AUDIO_POLICY_MUTE,
    )
    voiceover = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1"], audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    )
    assert mute != voiceover


def test_run_key_omits_caption_policy_entirely_when_none_so_pre_captions_keys_are_unaffected():
    # Captions V1 was added after Assembly V2 already shipped - an assembly
    # computed without requesting captions must hash EXACTLY as it always
    # did, so already-completed assemblies remain valid/reusable.
    without_param = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1", "v2"], source_voice_asset_ids=["a1", "a2"],
        audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    )
    with_explicit_none = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1", "v2"], source_voice_asset_ids=["a1", "a2"],
        audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER, caption_policy=None,
    )
    assert without_param == with_explicit_none


def test_run_key_is_sensitive_to_the_caption_policy_when_captions_are_requested():
    without_captions = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1"], audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    )
    with_captions = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1"], audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
        caption_policy="captions-burned-v1",
    )
    assert without_captions != with_captions


def test_run_key_is_sensitive_to_which_caption_policy_string_is_given():
    a = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1"], audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
        caption_policy="captions-burned-v1",
    )
    b = compute_assembly_run_key(
        production_id=PRODUCTION_ID, source_asset_ids=["v1"], audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
        caption_policy="captions-burned-v2",
    )
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


# --- get_completed_voice_assets ---------------------------------------------------------------------


def test_get_completed_voice_assets_returns_one_entry_per_scene_in_scene_order():
    db = seeded_assembly_db_with_voice(scene_numbers=(3, 1, 2))
    voices = AssemblyRepository(db).get_completed_voice_assets(PRODUCTION_ID)
    assert [v.scene_number for v in voices] == [1, 2, 3]


def test_get_completed_voice_assets_carries_the_storage_reference_and_duration():
    db = seeded_assembly_db_with_voice(scene_numbers=(1,), voice_kwargs={"duration_seconds": 7.5})
    [voice] = AssemblyRepository(db).get_completed_voice_assets(PRODUCTION_ID)
    assert voice.storage_bucket == "media-assets"
    assert voice.storage_path.endswith(".wav")
    assert voice.duration_seconds == 7.5


def test_get_completed_voice_assets_resolves_to_the_latest_completed_row():
    db = seeded_assembly_db_with_voice(scene_numbers=(1,))
    db.tables["media_assets"].append(
        completed_voice_asset_row(1, asset_id="aasset-1-old", created_at="2025-01-01T00:00:00+00:00")
    )
    voices = AssemblyRepository(db).get_completed_voice_assets(PRODUCTION_ID)
    assert len(voices) == 1 and voices[0].id == "aasset-1"


def test_get_completed_voice_assets_never_returns_video_or_image_assets():
    db = seeded_assembly_db_with_voice(scene_numbers=(1,))  # already has a completed video for scene 1
    voices = AssemblyRepository(db).get_completed_voice_assets(PRODUCTION_ID)
    assert len(voices) == 1 and voices[0].id == "aasset-1"


def test_get_completed_voice_assets_never_returns_ambient_or_music_audio_subtypes():
    db = seeded_assembly_db_with_voice(scene_numbers=(1,))
    db.tables["media_assets"].append({
        **completed_voice_asset_row(1, asset_id="ambient-1", created_at="2026-01-03T00:00:00+00:00"),
        "audio_subtype": "ambient",
    })
    voices = AssemblyRepository(db).get_completed_voice_assets(PRODUCTION_ID)
    assert len(voices) == 1 and voices[0].id == "aasset-1"


def test_a_scene_with_no_completed_voice_at_all_is_simply_absent():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2), voice_scene_numbers=(1,))
    voices = AssemblyRepository(db).get_completed_voice_assets(PRODUCTION_ID)
    assert [v.scene_number for v in voices] == [1]


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
    # A plain V1 (video-only) record defaults to no voice ids and the mute policy.
    assert row["source_voice_asset_ids"] == []
    assert row["audio_policy"] == ASSEMBLY_AUDIO_POLICY_MUTE


def test_a_v2_voiceover_synced_assembly_persists_its_voice_ids_policy_and_scene_manifest():
    db = seeded_assembly_db_with_voice(scene_numbers=(1, 2))
    repo = AssemblyRepository(db)
    manifest = (
        {
            "scene_number": 1, "video_asset_id": "vasset-1", "voice_asset_id": "aasset-1",
            "video_duration_seconds": 4.0, "voice_duration_seconds": 6.0, "scene_duration_seconds": 6.0,
            "sync_strategy": "video_extended_frozen_frame",
        },
        {
            "scene_number": 2, "video_asset_id": "vasset-2", "voice_asset_id": "aasset-2",
            "video_duration_seconds": 6.0, "voice_duration_seconds": 5.0, "scene_duration_seconds": 6.0,
            "sync_strategy": "audio_padded_with_silence",
        },
    )
    record = AssemblyRecord(
        status=AssemblyStatus.COMPLETED,
        run_key=compute_assembly_run_key(
            production_id=PRODUCTION_ID, source_asset_ids=["vasset-1", "vasset-2"],
            source_voice_asset_ids=["aasset-1", "aasset-2"], audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER,
        ),
        source_asset_ids=("vasset-1", "vasset-2"), source_scene_count=2, source_duration_seconds=10.0,
        output_duration_seconds=12.0, output_storage_bucket="media-assets",
        output_storage_path="p/assembly/x.mp4", output_storage_url="https://x/p/assembly/x.mp4",
        output_mime_type="video/mp4", source_voice_asset_ids=("aasset-1", "aasset-2"),
        audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER, scene_manifest=manifest,
    )
    assembly_id = repo.save_assembly(production_id=PRODUCTION_ID, record=record)
    row = repo.get_assembly(assembly_id)
    assert row["source_voice_asset_ids"] == ["aasset-1", "aasset-2"]
    assert row["audio_policy"] == ASSEMBLY_AUDIO_POLICY_VOICEOVER
    assert row["scene_manifest"] == list(manifest)


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
