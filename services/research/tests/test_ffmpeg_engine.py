"""FFmpeg concatenation engine: pure command construction/parsing, the
orchestration in `FFmpegAssembler.assemble()` (via a fake, injected
`CommandRunner` - never a real subprocess), plus one real-ffmpeg integration
test (skipped if ffmpeg is not installed) using tiny synthetically-generated
clips - never the user's real Supabase-stored assets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from research.ffmpeg_engine import (
    DEFAULT_CRF,
    DEFAULT_FPS,
    SYNC_STRATEGY_AUDIO_PADDED_WITH_SILENCE,
    SYNC_STRATEGY_EQUAL_NO_ADJUSTMENT,
    SYNC_STRATEGY_VIDEO_EXTENDED_FROZEN_FRAME,
    AssembledOutput,
    AssembledOutputV2,
    CaptionCue,
    CommandResult,
    FFmpegAssembler,
    FFmpegNotFoundError,
    FFmpegProbeError,
    FFmpegProcessError,
    SceneMediaInput,
    SyncedSceneResult,
    build_audio_video_concat_command,
    build_audio_video_concat_filter_complex,
    build_caption_burn_command,
    build_caption_overlay_filter_complex,
    build_concat_command,
    build_concat_filter_complex,
    build_probe_duration_command,
    build_probe_resolution_command,
    build_scale_pad_filter,
    build_scene_sync_command,
    build_scene_sync_filter_complex,
    parse_probe_duration,
    parse_probe_resolution,
    sync_strategy_for,
)

FFMPEG_INSTALLED = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- pure command construction ---------------------------------------------------------------------


def test_build_scale_pad_filter_produces_the_expected_chain():
    assert build_scale_pad_filter(0, 1280, 720) == (
        "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v0]"
    )


def test_build_scale_pad_filter_uses_the_given_index_and_fps():
    assert "[2:v]" in build_scale_pad_filter(2, 100, 200, fps=24)
    assert "fps=24" in build_scale_pad_filter(2, 100, 200, fps=24)
    assert "[v2]" in build_scale_pad_filter(2, 100, 200, fps=24)


def test_build_concat_filter_complex_joins_every_input_in_order():
    result = build_concat_filter_complex(3, 1280, 720)
    assert result.count(";") == 3  # 3 per-input chains + 1 concat line, joined by 3 semicolons
    assert result.endswith("[v0][v1][v2]concat=n=3:v=1:a=0[outv]")


def test_build_concat_filter_complex_rejects_zero_inputs():
    with pytest.raises(ValueError):
        build_concat_filter_complex(0, 1280, 720)


def test_build_concat_command_shape():
    command = build_concat_command(
        "ffmpeg", ["a.mp4", "b.mp4"], "out.mp4", width=1280, height=720,
    )
    assert command[0] == "ffmpeg"
    assert command[1] == "-y"
    assert command.count("-i") == 2
    assert "a.mp4" in command and "b.mp4" in command
    assert "-filter_complex" in command
    assert command[command.index("-filter_complex") + 1] == build_concat_filter_complex(2, 1280, 720)
    assert command[command.index("-map") + 1] == "[outv]"
    assert command[-1] == "out.mp4"
    assert "-c:v" in command and command[command.index("-c:v") + 1] == "libx264"
    assert "-crf" in command and command[command.index("-crf") + 1] == str(DEFAULT_CRF)
    assert "-movflags" in command and command[command.index("-movflags") + 1] == "+faststart"


def test_build_concat_command_is_deterministic():
    a = build_concat_command("ffmpeg", ["a.mp4", "b.mp4"], "out.mp4", width=1280, height=720)
    b = build_concat_command("ffmpeg", ["a.mp4", "b.mp4"], "out.mp4", width=1280, height=720)
    assert a == b


def test_build_concat_command_rejects_no_inputs():
    with pytest.raises(ValueError):
        build_concat_command("ffmpeg", [], "out.mp4", width=1280, height=720)


def test_build_probe_commands_target_the_given_binary_and_file():
    assert build_probe_resolution_command("ffprobe", "clip.mp4")[0] == "ffprobe"
    assert build_probe_resolution_command("ffprobe", "clip.mp4")[-1] == "clip.mp4"
    assert build_probe_duration_command("ffprobe", "clip.mp4")[-1] == "clip.mp4"


def test_parse_probe_resolution():
    assert parse_probe_resolution(b"1280x720\n") == (1280, 720)


def test_parse_probe_resolution_rejects_unparseable_output():
    with pytest.raises(FFmpegProbeError, match="could not parse a resolution"):
        parse_probe_resolution(b"not a resolution")


def test_parse_probe_duration():
    assert parse_probe_duration(b"23.977000\n") == pytest.approx(23.977)


def test_parse_probe_duration_rejects_unparseable_output():
    with pytest.raises(FFmpegProbeError, match="could not parse a duration"):
        parse_probe_duration(b"N/A\n")


# --- FFmpegAssembler.assemble() orchestration, via a fake CommandRunner ----------------------------


class ScriptedRunner:
    """Simulates ffmpeg/ffprobe well enough for `assemble()`'s orchestration
    to be tested without a real subprocess: recognises which command it was
    given by its first argument, and - for the concat command - actually
    writes bytes to the expected output path, exactly as real ffmpeg would."""

    def __init__(self, *, resolution=(1280, 720), duration=12.0, ffmpeg_returncode=0, ffmpeg_stderr=b"",
                 skip_output_write=False):
        self.resolution = resolution
        self.duration = duration
        self.ffmpeg_returncode = ffmpeg_returncode
        self.ffmpeg_stderr = ffmpeg_stderr
        self.skip_output_write = skip_output_write
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> CommandResult:
        self.calls.append(args)
        if "-show_entries" in args and "stream=width,height" in args:
            w, h = self.resolution
            return CommandResult(0, f"{w}x{h}\n".encode(), b"")
        if "-show_entries" in args and "format=duration" in args:
            return CommandResult(0, f"{self.duration}\n".encode(), b"")
        # the concat (ffmpeg) command: the output path is always the last argument
        if self.ffmpeg_returncode == 0 and not self.skip_output_write:
            Path(args[-1]).write_bytes(b"fake-assembled-mp4-bytes")
        return CommandResult(self.ffmpeg_returncode, b"", self.ffmpeg_stderr)


def test_a_successful_assembly_returns_the_output_with_measured_duration():
    runner = ScriptedRunner(resolution=(1280, 720), duration=18.5)
    assembler = FFmpegAssembler(run=runner)
    result = assembler.assemble([b"clip-a", b"clip-b", b"clip-c"])
    assert isinstance(result, AssembledOutput)
    assert result.data == b"fake-assembled-mp4-bytes"
    assert result.mime_type == "video/mp4"
    assert result.output_duration_seconds == pytest.approx(18.5)
    assert result.metadata["width"] == 1280 and result.metadata["height"] == 720
    assert result.metadata["input_count"] == 3


def test_the_first_clips_resolution_is_used_as_the_common_target():
    runner = ScriptedRunner(resolution=(720, 1280))  # vertical, e.g. a real Veo 9:16 clip
    FFmpegAssembler(run=runner).assemble([b"clip-a", b"clip-b"])
    ffmpeg_call = next(c for c in runner.calls if "-filter_complex" in c)
    assert "scale=720:1280" in ffmpeg_call[ffmpeg_call.index("-filter_complex") + 1]


def test_assemble_requires_at_least_one_clip():
    with pytest.raises(ValueError):
        FFmpegAssembler(run=ScriptedRunner()).assemble([])


def test_ffmpeg_process_failure_is_a_process_error_with_stderr_tail():
    runner = ScriptedRunner(ffmpeg_returncode=1, ffmpeg_stderr=b"Unknown encoder 'libx264'")
    with pytest.raises(FFmpegProcessError) as info:
        FFmpegAssembler(run=runner).assemble([b"clip-a", b"clip-b"])
    assert info.value.returncode == 1
    assert "Unknown encoder" in info.value.stderr_tail


def test_ffmpeg_success_but_no_output_file_is_still_a_process_error():
    runner = ScriptedRunner(skip_output_write=True)
    with pytest.raises(FFmpegProcessError, match="produced no output file"):
        FFmpegAssembler(run=runner).assemble([b"clip-a"])


def test_a_probe_failure_on_the_first_clip_is_a_probe_error():
    def failing_probe(args: list[str]) -> CommandResult:
        if "-show_entries" in args and "stream=width,height" in args:
            return CommandResult(1, b"", b"ffprobe: no such file")
        return CommandResult(0, b"12.0\n", b"")

    with pytest.raises(FFmpegProbeError):
        FFmpegAssembler(run=failing_probe).assemble([b"clip-a"])


def test_from_settings_uses_the_configured_binaries():
    from research.config import Settings

    settings = Settings.from_env({"FFMPEG_PATH": "/custom/ffmpeg", "FFPROBE_PATH": "/custom/ffprobe"})
    runner = ScriptedRunner()
    FFmpegAssembler.from_settings(settings, run=runner).assemble([b"clip-a"])
    assert all(c[0] in ("/custom/ffmpeg", "/custom/ffprobe") for c in runner.calls)


def test_a_missing_binary_is_a_clear_not_found_error_not_a_bare_os_error():
    # ffprobe is called first (to detect the target resolution), so it must
    # also be pointed at a nonexistent path to isolate "the binary itself is
    # missing" from "ffprobe ran but the input wasn't a real video".
    assembler = FFmpegAssembler(
        ffmpeg_path="definitely-not-a-real-binary-xyz", ffprobe_path="definitely-not-a-real-binary-xyz",
    )
    with pytest.raises(FFmpegNotFoundError, match="install ffmpeg"):
        assembler.assemble([b"clip-a"])


# --- Assembly V2: sync_strategy_for --------------------------------------------------------------------


def test_sync_strategy_video_longer_pads_audio_with_silence():
    assert sync_strategy_for(8.0, 5.0) == SYNC_STRATEGY_AUDIO_PADDED_WITH_SILENCE


def test_sync_strategy_voice_longer_extends_video():
    assert sync_strategy_for(5.0, 8.0) == SYNC_STRATEGY_VIDEO_EXTENDED_FROZEN_FRAME


def test_sync_strategy_equal_durations_applies_neither():
    assert sync_strategy_for(6.0, 6.0) == SYNC_STRATEGY_EQUAL_NO_ADJUSTMENT


def test_sync_strategy_within_tolerance_counts_as_equal():
    assert sync_strategy_for(6.0, 6.004) == SYNC_STRATEGY_EQUAL_NO_ADJUSTMENT


# --- Assembly V2: pure command construction --------------------------------------------------------


def test_build_scene_sync_filter_complex_never_references_source_video_audio():
    # The mute-source-audio policy, structurally enforced: [0:a] (the
    # video's own audio) must never appear anywhere in this filter graph,
    # whichever branch (pad/extend/neither) is taken.
    for video_extend, audio_pad in [(0.0, 0.0), (0.0, 3.0), (2.0, 0.0)]:
        result = build_scene_sync_filter_complex(
            width=1080, height=1920, video_extend_seconds=video_extend, audio_pad_seconds=audio_pad
        )
        assert "[0:a]" not in result
        assert "[1:a]" in result  # the voiceover input IS used


def test_build_scene_sync_filter_complex_pads_audio_when_video_is_longer():
    result = build_scene_sync_filter_complex(
        width=1080, height=1920, video_extend_seconds=0.0, audio_pad_seconds=2.5
    )
    assert "apad=pad_dur=2.500" in result
    assert "tpad" not in result
    assert "anull" not in result


def test_build_scene_sync_filter_complex_extends_video_when_voice_is_longer():
    result = build_scene_sync_filter_complex(
        width=1080, height=1920, video_extend_seconds=2.0, audio_pad_seconds=0.0
    )
    assert "tpad=stop_mode=clone:stop_duration=2.000" in result
    assert "apad" not in result
    assert "anull" in result  # the audio passes through unmodified


def test_build_scene_sync_filter_complex_equal_durations_applies_neither_filter():
    result = build_scene_sync_filter_complex(
        width=1080, height=1920, video_extend_seconds=0.0, audio_pad_seconds=0.0
    )
    assert "tpad" not in result
    assert "apad" not in result
    assert "anull" in result


def test_build_scene_sync_command_shape():
    command = build_scene_sync_command(
        "ffmpeg", "video.mp4", "voice.wav", "synced.mp4",
        width=1080, height=1920, scene_duration_seconds=8.0, video_extend_seconds=2.0, audio_pad_seconds=0.0,
    )
    assert command[0] == "ffmpeg" and command[1] == "-y"
    # video input first, voice input second - build_scene_sync_filter_complex assumes this order ([0:v]/[1:a]).
    i_indices = [i for i, a in enumerate(command) if a == "-i"]
    assert command[i_indices[0] + 1] == "video.mp4"
    assert command[i_indices[1] + 1] == "voice.wav"
    assert command[command.index("-map") + 1] == "[vout]"
    assert command.count("-map") == 2 and "[aout]" in command
    assert command[command.index("-t") + 1] == "8.000"
    assert "-c:v" in command and command[command.index("-c:v") + 1] == "libx264"
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "aac"
    assert command[-1] == "synced.mp4"


def test_build_audio_video_concat_filter_complex_joins_video_and_audio_for_every_input():
    result = build_audio_video_concat_filter_complex(3)
    assert result == "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[outv][outa]"


def test_build_audio_video_concat_filter_complex_rejects_zero_inputs():
    with pytest.raises(ValueError):
        build_audio_video_concat_filter_complex(0)


def test_build_audio_video_concat_command_shape():
    command = build_audio_video_concat_command("ffmpeg", ["a.mp4", "b.mp4"], "out.mp4")
    assert command.count("-i") == 2
    assert command[command.index("-map") + 1] == "[outv]"
    assert "[outa]" in command
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "aac"
    assert "-movflags" in command and command[command.index("-movflags") + 1] == "+faststart"
    assert command[-1] == "out.mp4"


def test_build_audio_video_concat_command_rejects_no_inputs():
    with pytest.raises(ValueError):
        build_audio_video_concat_command("ffmpeg", [], "out.mp4")


# --- Assembly V2: assemble_with_voiceover() orchestration, via a fake CommandRunner -----------------


class ScriptedRunnerV2:
    """Simulates ffmpeg/ffprobe for `assemble_with_voiceover()`'s
    orchestration: recognises which probe/encode step it was given by its
    arguments and the (workdir-relative) basename of the file it targets -
    `research.ffmpeg_engine` always names its temp files `video-N.mp4`,
    `voice-N.audio`, `synced-N.mp4` and `assembled.mp4` (see
    `FFmpegAssembler.assemble_with_voiceover`)."""

    def __init__(
        self, *, resolution=(1080, 1920), video_durations, voice_durations, final_duration=None,
        ffmpeg_returncode=0, ffmpeg_stderr=b"",
    ):
        self.resolution = resolution
        self.video_durations = list(video_durations)
        self.voice_durations = list(voice_durations)
        self.final_duration = final_duration
        self.ffmpeg_returncode = ffmpeg_returncode
        self.ffmpeg_stderr = ffmpeg_stderr
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> CommandResult:
        self.calls.append(args)
        if "-show_entries" in args and "stream=width,height" in args:
            w, h = self.resolution
            return CommandResult(0, f"{w}x{h}\n".encode(), b"")
        if "-show_entries" in args and "format=duration" in args:
            name = os.path.basename(args[-1])
            if name.startswith("video-"):
                dur = self.video_durations[int(name[len("video-"):-len(".mp4")])]
            elif name.startswith("voice-"):
                dur = self.voice_durations[int(name[len("voice-"):-len(".audio")])]
            elif name.startswith("synced-"):
                i = int(name[len("synced-"):-len(".mp4")])
                dur = max(self.video_durations[i], self.voice_durations[i])
            elif name == "assembled.mp4":
                dur = self.final_duration if self.final_duration is not None else sum(
                    max(v, a) for v, a in zip(self.video_durations, self.voice_durations)
                )
            else:
                raise AssertionError(f"unexpected ffprobe target: {name}")
            return CommandResult(0, f"{dur}\n".encode(), b"")
        # an ffmpeg encode command (either a per-scene sync or the final concat) - the output path
        # is always the last argument, exactly as the real binary would produce it.
        if self.ffmpeg_returncode == 0:
            Path(args[-1]).write_bytes(b"fake-bytes")
        return CommandResult(self.ffmpeg_returncode, b"", self.ffmpeg_stderr)


def scenes(*pairs):
    """`pairs` is (scene_number, video_bytes_marker, voice_bytes_marker) tuples."""
    return [
        SceneMediaInput(scene_number=n, video_bytes=f"video-{n}".encode(), voice_bytes=f"voice-{n}".encode())
        for n, *_ in pairs
    ]


def test_assemble_with_voiceover_requires_at_least_one_scene():
    with pytest.raises(ValueError):
        FFmpegAssembler(run=ScriptedRunnerV2(video_durations=[], voice_durations=[])).assemble_with_voiceover([])


def test_voice_shorter_than_video_pads_silence_keeps_full_video_length():
    runner = ScriptedRunnerV2(video_durations=[8.0], voice_durations=[5.0])
    result = FFmpegAssembler(run=runner).assemble_with_voiceover(scenes((1,)))
    assert isinstance(result, AssembledOutputV2)
    [scene_result] = result.scene_results
    assert scene_result.sync_strategy == SYNC_STRATEGY_AUDIO_PADDED_WITH_SILENCE
    assert scene_result.scene_duration_seconds == pytest.approx(8.0)
    sync_call = next(c for c in runner.calls if "-filter_complex" in c and "synced-0.mp4" == os.path.basename(c[-1]))
    filter_complex = sync_call[sync_call.index("-filter_complex") + 1]
    assert "apad=pad_dur=3.000" in filter_complex
    assert "tpad" not in filter_complex


def test_voice_longer_than_video_freezes_final_frame_never_cuts_narration():
    runner = ScriptedRunnerV2(video_durations=[4.0], voice_durations=[7.5])
    result = FFmpegAssembler(run=runner).assemble_with_voiceover(scenes((1,)))
    [scene_result] = result.scene_results
    assert scene_result.sync_strategy == SYNC_STRATEGY_VIDEO_EXTENDED_FROZEN_FRAME
    assert scene_result.scene_duration_seconds == pytest.approx(7.5)
    sync_call = next(c for c in runner.calls if "-filter_complex" in c and "synced-0.mp4" == os.path.basename(c[-1]))
    filter_complex = sync_call[sync_call.index("-filter_complex") + 1]
    assert "tpad=stop_mode=clone:stop_duration=3.500" in filter_complex
    assert "apad" not in filter_complex


def test_equal_durations_needs_no_adjustment():
    runner = ScriptedRunnerV2(video_durations=[6.0], voice_durations=[6.0])
    result = FFmpegAssembler(run=runner).assemble_with_voiceover(scenes((1,)))
    [scene_result] = result.scene_results
    assert scene_result.sync_strategy == SYNC_STRATEGY_EQUAL_NO_ADJUSTMENT


def test_source_video_audio_is_never_referenced_across_any_scene():
    runner = ScriptedRunnerV2(video_durations=[4.0, 8.0, 6.0], voice_durations=[6.0, 5.0, 6.0])
    FFmpegAssembler(run=runner).assemble_with_voiceover(scenes((1,), (2,), (3,)))
    sync_calls = [c for c in runner.calls if "-filter_complex" in c and c[-1].endswith(("synced-0.mp4", "synced-1.mp4", "synced-2.mp4"))]
    assert len(sync_calls) == 3
    for call in sync_calls:
        assert "[0:a]" not in call[call.index("-filter_complex") + 1]


def test_scenes_are_processed_and_concatenated_in_the_given_order():
    runner = ScriptedRunnerV2(video_durations=[4.0, 6.0, 8.0], voice_durations=[4.0, 6.0, 8.0])
    result = FFmpegAssembler(run=runner).assemble_with_voiceover(scenes((3,), (1,), (2,)))
    assert [r.scene_number for r in result.scene_results] == [3, 1, 2]
    concat_call = next(c for c in runner.calls if "-filter_complex" in c and os.path.basename(c[-1]) == "assembled.mp4")
    # three synced inputs, in the order they were passed in (caller - research.assembly_v2 - is
    # responsible for scene ordering before calling this method).
    assert concat_call.count("-i") == 3


def test_output_duration_is_measured_from_the_final_concatenated_file_not_summed():
    runner = ScriptedRunnerV2(video_durations=[4.0, 6.0], voice_durations=[4.0, 6.0], final_duration=10.3)
    result = FFmpegAssembler(run=runner).assemble_with_voiceover(scenes((1,), (2,)))
    assert result.output_duration_seconds == pytest.approx(10.3)


def test_the_first_scenes_video_resolution_is_used_as_the_common_target():
    runner = ScriptedRunnerV2(resolution=(720, 1280), video_durations=[6.0], voice_durations=[6.0])
    result = FFmpegAssembler(run=runner).assemble_with_voiceover(scenes((1,)))
    assert result.metadata["width"] == 720 and result.metadata["height"] == 1280


def test_a_scene_sync_failure_is_a_process_error_naming_the_scene():
    runner = ScriptedRunnerV2(
        video_durations=[6.0], voice_durations=[6.0], ffmpeg_returncode=1, ffmpeg_stderr=b"Unknown encoder 'aac'"
    )
    with pytest.raises(FFmpegProcessError, match="scene 1"):
        FFmpegAssembler(run=runner).assemble_with_voiceover(scenes((1,)))


def test_a_concat_failure_after_successful_scene_sync_is_still_a_process_error():
    class FailOnConcat(ScriptedRunnerV2):
        def __call__(self, args):
            if "-filter_complex" in args and os.path.basename(args[-1]) == "assembled.mp4":
                self.calls.append(args)
                return CommandResult(1, b"", b"concat boom")
            return super().__call__(args)

    runner = FailOnConcat(video_durations=[6.0, 6.0], voice_durations=[6.0, 6.0])
    with pytest.raises(FFmpegProcessError, match="concatenating synced scenes"):
        FFmpegAssembler(run=runner).assemble_with_voiceover(scenes((1,), (2,)))


# --- Captions V1: pure command construction ----------------------------------------------------------


def test_build_caption_overlay_filter_complex_chains_one_overlay_per_cue():
    result = build_caption_overlay_filter_complex([(0.0, 1.5), (1.5, 3.0), (3.0, 4.5)])
    assert result == (
        "[0:v][1:v]overlay=enable='between(t,0.000,1.500)'[vcap0];"
        "[vcap0][2:v]overlay=enable='between(t,1.500,3.000)'[vcap1];"
        "[vcap1][3:v]overlay=enable='between(t,3.000,4.500)'[vout]"
    )


def test_build_caption_overlay_filter_complex_single_cue():
    result = build_caption_overlay_filter_complex([(0.0, 2.0)])
    assert result == "[0:v][1:v]overlay=enable='between(t,0.000,2.000)'[vout]"


def test_build_caption_overlay_filter_complex_rejects_no_cues():
    with pytest.raises(ValueError):
        build_caption_overlay_filter_complex([])


def test_build_caption_burn_command_shape():
    command = build_caption_burn_command(
        "ffmpeg", "video.mp4", ["cap0.png", "cap1.png"], "out.mp4", cues=[(0.0, 1.0), (1.0, 2.0)],
    )
    assert command[0] == "ffmpeg" and command[1] == "-y"
    i_indices = [i for i, a in enumerate(command) if a == "-i"]
    assert command[i_indices[0] + 1] == "video.mp4"
    assert command[i_indices[1] + 1] == "cap0.png"
    assert command[i_indices[2] + 1] == "cap1.png"
    assert command[command.index("-map") + 1] == "[vout]"
    assert command.count("-map") == 2 and "0:a" in command
    assert "-c:v" in command and command[command.index("-c:v") + 1] == "libx264"
    # Audio is passed through unchanged - captions never touch it.
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "copy"
    assert "-movflags" in command and command[command.index("-movflags") + 1] == "+faststart"
    assert command[-1] == "out.mp4"


def test_build_caption_burn_command_rejects_no_images():
    with pytest.raises(ValueError):
        build_caption_burn_command("ffmpeg", "video.mp4", [], "out.mp4", cues=[])


def test_build_caption_burn_command_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        build_caption_burn_command("ffmpeg", "video.mp4", ["cap0.png"], "out.mp4", cues=[(0.0, 1.0), (1.0, 2.0)])


# --- Captions V1: burn_captions() orchestration, via a fake CommandRunner -----------------------------
# (Pillow's own PNG rendering is real here - it is fast, pure, and needs no
# mocking; only the ffmpeg subprocess itself is faked.)


class ScriptedCaptionRunner:
    def __init__(self, *, duration=3.0, ffmpeg_returncode=0, ffmpeg_stderr=b"", skip_output_write=False):
        self.duration = duration
        self.ffmpeg_returncode = ffmpeg_returncode
        self.ffmpeg_stderr = ffmpeg_stderr
        self.skip_output_write = skip_output_write
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> CommandResult:
        self.calls.append(args)
        if "-show_entries" in args and "format=duration" in args:
            return CommandResult(0, f"{self.duration}\n".encode(), b"")
        if self.ffmpeg_returncode == 0 and not self.skip_output_write:
            Path(args[-1]).write_bytes(b"fake-captioned-bytes")
        return CommandResult(self.ffmpeg_returncode, b"", self.ffmpeg_stderr)


def test_burn_captions_requires_at_least_one_cue():
    with pytest.raises(ValueError):
        FFmpegAssembler(run=ScriptedCaptionRunner()).burn_captions(b"video-bytes", [], width=480, height=854)


def test_burn_captions_returns_measured_duration_and_metadata():
    runner = ScriptedCaptionRunner(duration=4.2)
    cues = [CaptionCue("Hello there.", 0.0, 2.0), CaptionCue("General Kenobi.", 2.0, 4.2)]
    result = FFmpegAssembler(run=runner).burn_captions(b"video-bytes", cues, width=480, height=854)
    assert isinstance(result, AssembledOutput)
    assert result.mime_type == "video/mp4"
    assert result.output_duration_seconds == pytest.approx(4.2)
    assert result.metadata["width"] == 480 and result.metadata["height"] == 854
    assert result.metadata["caption_count"] == 2


def test_burn_captions_passes_one_png_input_per_cue_in_order():
    runner = ScriptedCaptionRunner()
    cues = [CaptionCue("First.", 0.0, 1.0), CaptionCue("Second.", 1.0, 2.0), CaptionCue("Third.", 2.0, 3.0)]
    FFmpegAssembler(run=runner).burn_captions(b"video-bytes", cues, width=480, height=854)
    encode_call = next(c for c in runner.calls if "-filter_complex" in c)
    assert encode_call.count("-i") == 4  # base video + 3 caption PNGs


def test_burn_captions_failure_is_a_process_error_naming_captions():
    runner = ScriptedCaptionRunner(ffmpeg_returncode=1, ffmpeg_stderr=b"boom")
    cues = [CaptionCue("Hello.", 0.0, 1.0)]
    with pytest.raises(FFmpegProcessError, match="captions"):
        FFmpegAssembler(run=runner).burn_captions(b"video-bytes", cues, width=480, height=854)


def test_burn_captions_success_but_no_output_file_is_still_a_process_error():
    runner = ScriptedCaptionRunner(skip_output_write=True)
    cues = [CaptionCue("Hello.", 0.0, 1.0)]
    with pytest.raises(FFmpegProcessError, match="no captioned output"):
        FFmpegAssembler(run=runner).burn_captions(b"video-bytes", cues, width=480, height=854)


# --- real ffmpeg integration test (skipped if ffmpeg is not installed) -----------------------------


@pytest.mark.skipif(not FFMPEG_INSTALLED, reason="ffmpeg/ffprobe not installed on PATH")
def test_real_ffmpeg_concatenates_two_synthetic_clips_into_one_playable_mp4(tmp_path):
    """The one test that exercises the REAL ffmpeg binary, confirming the
    command syntax this module builds is actually valid - not just
    plausible. Uses ffmpeg's own `lavfi` test-pattern generator for the two
    source clips (deliberately different sizes, to also exercise the
    scale/pad normalisation), never a real Veo/Gemini asset."""

    def make_clip(path: Path, *, size: str, seconds: int) -> bytes:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={DEFAULT_FPS}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(seconds), str(path),
            ],
            check=True, capture_output=True,
        )
        return path.read_bytes()

    clip_a = make_clip(tmp_path / "a.mp4", size="640x480", seconds=1)
    clip_b = make_clip(tmp_path / "b.mp4", size="320x240", seconds=1)  # different size than clip_a

    result = FFmpegAssembler().assemble([clip_a, clip_b])

    assert result.mime_type == "video/mp4"
    assert len(result.data) > 0
    # ~2 seconds total; generous tolerance for encoder/container overhead.
    assert 1.5 <= result.output_duration_seconds <= 3.0

    # And the output is genuinely a valid, playable video file: probe it for real.
    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(result.data)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", str(output_path)],
        check=True, capture_output=True,
    )
    assert float(probe.stdout.strip()) > 0


@pytest.mark.skipif(not FFMPEG_INSTALLED, reason="ffmpeg/ffprobe not installed on PATH")
def test_real_ffmpeg_synchronises_and_concatenates_two_scenes_with_voiceover(tmp_path):
    """The one test that exercises the REAL ffmpeg binary for
    `assemble_with_voiceover()`. Scene 1's voiceover is longer than its
    video (must extend the video, never cut narration); scene 2's voiceover
    is shorter than its video (must pad with silence, keep the full video).
    Never a real Veo/Gemini asset - `lavfi` test patterns and tones only."""

    def make_video(path: Path, *, seconds: float) -> bytes:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=480x854:rate={DEFAULT_FPS}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(seconds), str(path),
            ],
            check=True, capture_output=True,
        )
        return path.read_bytes()

    def make_voice(path: Path, *, seconds: float) -> bytes:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(path)],
            check=True, capture_output=True,
        )
        return path.read_bytes()

    scene1_video = make_video(tmp_path / "v1.mp4", seconds=2.0)
    scene1_voice = make_voice(tmp_path / "a1.wav", seconds=4.0)  # voice longer -> video must be extended
    scene2_video = make_video(tmp_path / "v2.mp4", seconds=3.0)
    scene2_voice = make_voice(tmp_path / "a2.wav", seconds=1.0)  # voice shorter -> audio padded with silence

    result = FFmpegAssembler().assemble_with_voiceover([
        SceneMediaInput(scene_number=1, video_bytes=scene1_video, voice_bytes=scene1_voice),
        SceneMediaInput(scene_number=2, video_bytes=scene2_video, voice_bytes=scene2_voice),
    ])

    assert isinstance(result, AssembledOutputV2)
    assert [r.sync_strategy for r in result.scene_results] == [
        SYNC_STRATEGY_VIDEO_EXTENDED_FROZEN_FRAME, SYNC_STRATEGY_AUDIO_PADDED_WITH_SILENCE,
    ]
    # scene 1 -> max(2, 4) = 4s; scene 2 -> max(3, 1) = 3s; total ~7s.
    assert 6.5 <= result.output_duration_seconds <= 7.5

    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(result.data)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(output_path)],
        check=True, capture_output=True,
    )
    stream_types = probe.stdout.decode().split()
    assert "video" in stream_types and "audio" in stream_types


@pytest.mark.skipif(not FFMPEG_INSTALLED, reason="ffmpeg/ffprobe not installed on PATH")
def test_real_ffmpeg_burns_real_pillow_rendered_captions_onto_a_video(tmp_path):
    """The one test that exercises the REAL ffmpeg binary AND real Pillow
    rendering for `burn_captions()` - this project's own ffmpeg build has no
    drawtext/libass support (see the module docstring), so this is the only
    way to prove the overlay-PNG technique actually produces a valid,
    playable captioned video, not just a plausible-looking command."""

    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration=3:size=480x854:rate={DEFAULT_FPS}",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(tmp_path / "base.mp4"),
        ],
        check=True, capture_output=True,
    )
    video_bytes = (tmp_path / "base.mp4").read_bytes()

    cues = [
        CaptionCue("Same job, same customer type quote?", 0.0, 1.5),
        CaptionCue("You are typing it out again from zero.", 1.5, 3.0),
    ]
    result = FFmpegAssembler().burn_captions(video_bytes, cues, width=480, height=854)

    assert isinstance(result, AssembledOutput)
    assert result.mime_type == "video/mp4"
    assert 2.5 <= result.output_duration_seconds <= 3.5

    output_path = tmp_path / "captioned.mp4"
    output_path.write_bytes(result.data)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(output_path)],
        check=True, capture_output=True,
    )
    stream_types = probe.stdout.decode().split()
    assert "video" in stream_types and "audio" in stream_types
