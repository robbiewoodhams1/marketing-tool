"""Build one Assembly's whole caption track: one scene at a time, using each
scene's own exact Production `voiceover` text and its own ACTUAL, measured
scene duration (never the production's originally requested duration - see
`research.assembly_v2`'s module docstring), then shift each scene's
scene-local segments onto the final, concatenated video's own timeline.

    Production voiceover text (per scene)
            +
    actual synchronised scene duration (per scene, from Assembly V2's own
    ffprobe-measured `SyncedSceneResult`)
            v
    research.caption_timing.segment_caption_text()  (scene-local segments)
            v
    build_caption_timeline()                        (shifted onto the final
                                                       video's timeline)
            v
    caption_manifest_payload()                       (persisted, inspectable,
                                                       reproducible JSON)

This module never touches ffmpeg or an image library - burning the result
into a video is `research.ffmpeg_engine.FFmpegAssembler.burn_captions`'s job,
which takes plain `CaptionCue`s and has no idea where they came from. That
split is deliberate: this module can be tested with nothing but arithmetic
and strings, and a future different burn-in technique needs no change here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from research.caption_timing import CAPTION_TIMING_ALGORITHM, segment_caption_text


@dataclass(frozen=True)
class SceneCaptionInput:
    """One scene's caption inputs, exactly as `research.assembly_v2` already
    has them to hand after synchronising video+voice: the scene's own exact
    voiceover text, and the ACTUAL (ffprobe-measured) duration that scene was
    synchronised to - never the scene's originally requested duration."""

    scene_number: int
    text: str
    duration_seconds: float


@dataclass(frozen=True)
class TimelineCaptionSegment:
    """One caption card on the FINAL, concatenated video's own timeline -
    `start_seconds`/`end_seconds` already include every preceding scene's
    actual duration, so this is exactly what `burn_captions` needs (via
    `to_caption_cue`), and exactly what the persisted manifest records."""

    index: int
    scene_number: int
    text: str
    start_seconds: float
    end_seconds: float


def build_caption_timeline(scenes: Sequence[SceneCaptionInput]) -> tuple[TimelineCaptionSegment, ...]:
    """One scene at a time, in the given order (the caller is responsible for
    scene ordering - see `research.assembly_v2`): segment that scene's own
    text over its own actual duration, then shift the result by the sum of
    every earlier scene's actual duration, so segment timestamps are correct
    against the FINAL, concatenated video - not scene-local ones.

    Example: scenes of actual duration 4s, 6s, 6s produce segments within
    [0, 4), [4, 10) and [10, 16) respectively (see the module docstring's
    worked example and `research.assembly_v2`'s own docstring for why scene
    durations are measured, never assumed from Production's request).

    Raises `research.caption_timing.CaptionError` if any scene's text/
    duration cannot be segmented (propagated unchanged - the caller decides
    how to handle it, see `research.assembly_v2.run_assembly_v2`)."""
    if not scenes:
        raise ValueError("build_caption_timeline() requires at least one scene")

    timeline: list[TimelineCaptionSegment] = []
    offset = 0.0
    counter = 0
    for scene in scenes:
        for local in segment_caption_text(scene.text, scene.duration_seconds):
            timeline.append(TimelineCaptionSegment(
                index=counter, scene_number=scene.scene_number, text=local.text,
                start_seconds=local.start_seconds + offset, end_seconds=local.end_seconds + offset,
            ))
            counter += 1
        offset += scene.duration_seconds
    return tuple(timeline)


def caption_manifest_payload(timeline: Sequence[TimelineCaptionSegment]) -> list[Mapping[str, Any]]:
    """The exact JSON shape persisted in `media_assemblies.caption_manifest`
    (see the Captions V1 migration) - one entry per segment, on the final
    video's own timeline, so it can be inspected/tested/reused without
    re-running Assembly. Includes the timing algorithm's own name so a future
    algorithm change is visible in already-stored data, not just in code."""
    return [
        {
            "index": seg.index,
            "scene_number": seg.scene_number,
            "text": seg.text,
            "start_seconds": seg.start_seconds,
            "end_seconds": seg.end_seconds,
            "timing_algorithm": CAPTION_TIMING_ALGORITHM,
        }
        for seg in timeline
    ]
