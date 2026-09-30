"""research.captions: shifting each scene's own caption segments onto the
final, concatenated video's timeline, and the persisted manifest shape."""

from __future__ import annotations

import pytest

from research.caption_timing import CaptionError
from research.captions import SceneCaptionInput, build_caption_timeline, caption_manifest_payload


# --- multi-scene offsetting (the worked example from the task) ---------------------------------------


def test_three_scenes_of_4_6_6_seconds_produce_the_documented_offsets():
    scenes = [
        SceneCaptionInput(scene_number=1, text="Scene one line here.", duration_seconds=4.0),
        SceneCaptionInput(scene_number=2, text="Scene two line here.", duration_seconds=6.0),
        SceneCaptionInput(scene_number=3, text="Scene three line here.", duration_seconds=6.0),
    ]
    timeline = build_caption_timeline(scenes)

    scene_1_segments = [s for s in timeline if s.scene_number == 1]
    scene_2_segments = [s for s in timeline if s.scene_number == 2]
    scene_3_segments = [s for s in timeline if s.scene_number == 3]

    assert scene_1_segments[0].start_seconds == 0.0
    assert scene_1_segments[-1].end_seconds == 4.0
    assert scene_2_segments[0].start_seconds == 4.0
    assert scene_2_segments[-1].end_seconds == 10.0
    assert scene_3_segments[0].start_seconds == 10.0
    assert scene_3_segments[-1].end_seconds == 16.0


def test_offsets_remain_correct_when_assembly_v2_extended_a_scene():
    # A scene whose ACTUAL synchronised duration (voice longer than video, so
    # Assembly V2 froze the final frame) differs from what was requested -
    # captions must follow the actual duration, never a requested one.
    scenes = [
        SceneCaptionInput(scene_number=1, text="Scene one.", duration_seconds=4.0),
        SceneCaptionInput(scene_number=2, text="Scene two, extended by voiceover sync.", duration_seconds=8.33),
        SceneCaptionInput(scene_number=3, text="Scene three.", duration_seconds=6.0),
    ]
    timeline = build_caption_timeline(scenes)
    scene_2 = [s for s in timeline if s.scene_number == 2]
    scene_3 = [s for s in timeline if s.scene_number == 3]
    assert scene_2[0].start_seconds == 4.0
    assert scene_2[-1].end_seconds == pytest.approx(12.33)
    assert scene_3[0].start_seconds == pytest.approx(12.33)
    assert scene_3[-1].end_seconds == pytest.approx(18.33)


def test_global_index_is_reassigned_contiguously_across_scenes():
    scenes = [
        SceneCaptionInput(scene_number=1, text="One. Two. Three sentences here for scene one alone.", duration_seconds=6.0),
        SceneCaptionInput(scene_number=2, text="Another scene entirely.", duration_seconds=4.0),
    ]
    timeline = build_caption_timeline(scenes)
    assert [s.index for s in timeline] == list(range(len(timeline)))


def test_segments_across_the_whole_timeline_never_overlap():
    scenes = [
        SceneCaptionInput(scene_number=1, text="Scene one has quite a bit more to say here.", duration_seconds=5.0),
        SceneCaptionInput(scene_number=2, text="Scene two also has plenty of narration going on.", duration_seconds=7.0),
    ]
    timeline = build_caption_timeline(scenes)
    for a, b in zip(timeline, timeline[1:]):
        assert a.end_seconds <= b.start_seconds


def test_scenes_are_processed_in_the_given_order_not_resorted():
    # The caller (research.assembly_v2) is responsible for scene ordering;
    # this module trusts the order it is given.
    scenes = [
        SceneCaptionInput(scene_number=3, text="Third scene number, first in the list.", duration_seconds=4.0),
        SceneCaptionInput(scene_number=1, text="First scene number, second in the list.", duration_seconds=4.0),
    ]
    timeline = build_caption_timeline(scenes)
    assert [s.scene_number for s in timeline[:1]] == [3]
    assert timeline[0].start_seconds == 0.0


def test_a_single_scene_still_works():
    scenes = [SceneCaptionInput(scene_number=1, text="Only one scene here.", duration_seconds=3.0)]
    timeline = build_caption_timeline(scenes)
    assert timeline[0].start_seconds == 0.0
    assert timeline[-1].end_seconds == 3.0


def test_requires_at_least_one_scene():
    with pytest.raises(ValueError):
        build_caption_timeline([])


def test_a_scenes_malformed_empty_text_propagates_as_a_caption_error():
    scenes = [
        SceneCaptionInput(scene_number=1, text="Fine scene text.", duration_seconds=4.0),
        SceneCaptionInput(scene_number=2, text="   ", duration_seconds=4.0),  # malformed: blank narration
    ]
    with pytest.raises(CaptionError):
        build_caption_timeline(scenes)


# --- manifest payload shape (what actually gets persisted) --------------------------------------------


def test_manifest_payload_has_one_entry_per_segment_with_the_expected_keys():
    scenes = [SceneCaptionInput(scene_number=1, text="Scene one line here.", duration_seconds=4.0)]
    timeline = build_caption_timeline(scenes)
    payload = caption_manifest_payload(timeline)
    assert len(payload) == len(timeline)
    [entry] = payload
    assert set(entry) == {"index", "scene_number", "text", "start_seconds", "end_seconds", "timing_algorithm"}
    assert entry["scene_number"] == 1
    assert entry["start_seconds"] == 0.0 and entry["end_seconds"] == 4.0


def test_manifest_payload_of_an_empty_timeline_is_an_empty_list():
    assert caption_manifest_payload(()) == []


def test_manifest_payload_is_plain_json_serialisable_data():
    import json

    scenes = [
        SceneCaptionInput(scene_number=1, text="Scene one.", duration_seconds=4.0),
        SceneCaptionInput(scene_number=2, text="Scene two.", duration_seconds=6.0),
    ]
    payload = caption_manifest_payload(build_caption_timeline(scenes))
    json.dumps(payload)  # must not raise
