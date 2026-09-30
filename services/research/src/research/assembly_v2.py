"""Terminal entry point and service: Assembly V2 - synchronise each scene of
one persisted Production against its own already-generated voiceover, then
concatenate the six synchronised scenes into a single finished MP4.

    python -m research.assembly_v2 --production-id PRODUCTION_ID

    load production -> load its completed VIDEO assets and its completed
    VOICEOVER assets (the CURRENT completed asset for each scene number,
    across any media run) -> require every scene to have BOTH -> download
    each pair from storage -> synchronise + concatenate via ffmpeg -> upload
    the result -> persist (success or failure) -> report

Assembly V2 is the SAME chapter as `research.assembly` (V1), still strictly
downstream of Media Generation and Voice Generation, never a peer to them:

    Production        -> WHAT should exist: the scenes and their order
    Media Generation   -> individual generated VIDEO/IMAGE assets
    Voice Generation   -> individual generated VOICEOVER assets
    Assembly (this)    -> combines already-generated assets into one
                          finished file. It makes NO AI provider calls of
                          its own (no Gemini, no Veo) and NEVER regenerates
                          media - not even when a scene's video and voice
                          durations disagree (see research.ffmpeg_engine's
                          module docstring for the deterministic ffmpeg
                          timing policy that handles that instead).

V1 (`research.assembly`) remains available, unchanged, for a production that
has no voiceover generated yet (or where a mute, picture-only result is
wanted) - its own completed rows remain valid, and a production may
accumulate rows from BOTH V1 and V2 over time (they are distinguished by
`audio_policy`; see `research.assembly_schema`). V2 supersedes V1 once
voiceover exists: it requires BOTH a completed video and a completed
voiceover for every scene - a scene with only one is refused up front,
exactly like V1 refuses a scene with no video.

AUDIO POLICY (V1): voiceover is the sole, authoritative spoken audio. The
source video's own generated audio (Veo scene audio) remains muted - not
mixed in, not preserved. Future Assembly versions may add source ambient
audio, background music, sound effects, and ducking; none of that is
attempted here (see research.ffmpeg_engine's module docstring for the full
audio-sync policy this version does implement).

CAPTIONS (V1, optional - `--captions`): burns each scene's own exact
Production `voiceover` text onto the video, using its ACTUAL synchronised
scene duration (the `SyncedSceneResult` `assemble_with_voiceover` already
measured - never the production's originally requested scene duration).
Captions are computed AFTER synchronisation/concatenation, using the same
principle laid out at the top of this module:

    Production determines the authoritative words.
    Voice Generation produces the audio.
    Captions represent those authoritative words visually.
    Assembly combines everything into the final video.

See `research.captions`/`research.caption_timing` for the deterministic
segmentation/timeline algorithm (no LLM, no ASR) and
`research.ffmpeg_engine.FFmpegAssembler.burn_captions` for the burn-in.
Disabled by default: an assembly run without `--captions` hashes and behaves
exactly as Assembly V2 always has (see `research.assembly_store
.compute_assembly_run_key`'s own docstring for why).

Needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, plus a working
`ffmpeg`/`ffprobe` on PATH (or FFMPEG_PATH/FFPROBE_PATH pointing at them).

Exit codes: 0 assembled now, or an identical assembly already existed; 1 the
production/its scenes cannot be assembled as they currently stand (missing
production, or one or more scenes with no completed video and/or no
completed voiceover asset); 2 bad arguments / missing configuration; 3
assembly was attempted and failed (source download / ffmpeg / output upload /
caption error).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from research.assembly_schema import (
    ASSEMBLY_AUDIO_POLICY_VOICEOVER,
    CAPTION_POLICY_BURNED_V1,
    AssemblyRecord,
    AssemblyStatus,
)
from research.assembly_store import AssemblyRepository, compute_assembly_run_key
from research.caption_timing import CaptionError
from research.captions import SceneCaptionInput, build_caption_timeline, caption_manifest_payload
from research.config import Settings
from research.ffmpeg_engine import CaptionCue, FFmpegAssembler, FFmpegError, SceneMediaInput, SyncedSceneResult
from research.logging import configure_logging, get_logger
from research.media_storage import (
    StorageError,
    SupabaseStorage,
    assembly_output_path,
    extension_for_mime_type,
)
from research.persistence import PersistenceConfigError, SupabaseDatabase

log = get_logger("research.assembly_v2")

FFMPEG_SETUP_HINT = (
    "install ffmpeg (e.g. `brew install ffmpeg` on macOS, `apt install ffmpeg` on Debian/Ubuntu) "
    "or set FFMPEG_PATH/FFPROBE_PATH to point at existing binaries"
)

Progress = Callable[[str], None]

EXIT_OK, EXIT_SOURCE, EXIT_CONFIG, EXIT_FAILED = 0, 1, 2, 3


class AssemblySourceError(Exception):
    """The production's scenes cannot be assembled as they currently stand. Nothing was changed."""


def _ignore(_: str) -> None:
    pass


@dataclass(frozen=True)
class AssemblyOutcomeV2:
    status: str  # "completed" | "exists" | "failed"
    assembly_id: str
    run_key: str
    source_scene_count: int
    source_duration_seconds: float | None
    output_duration_seconds: float | None = None
    output_url: str | None = None
    error_message: str | None = None
    scene_manifest: tuple[dict, ...] = field(default_factory=tuple)
    caption_manifest: tuple[dict, ...] | None = None


def _scene_manifest(
    scene_results: Sequence[SyncedSceneResult], *, video_ids: Sequence[str], voice_ids: Sequence[str]
) -> tuple[dict, ...]:
    return tuple(
        {
            "scene_number": r.scene_number,
            "video_asset_id": video_id,
            "voice_asset_id": voice_id,
            "video_duration_seconds": r.video_duration_seconds,
            "voice_duration_seconds": r.voice_duration_seconds,
            "scene_duration_seconds": r.scene_duration_seconds,
            "sync_strategy": r.sync_strategy,
        }
        for r, video_id, voice_id in zip(scene_results, video_ids, voice_ids, strict=True)
    )


def run_assembly_v2(
    production_id: str,
    *,
    repository: AssemblyRepository,
    storage: SupabaseStorage,
    assembler: FFmpegAssembler,
    progress: Progress = _ignore,
    captions: bool = False,
) -> AssemblyOutcomeV2:
    """Assemble `production_id`'s completed scene videos, synchronised
    against their own completed voiceovers, into one MP4 - optionally with
    each scene's own exact voiceover text burned in as captions (`captions`;
    see the module docstring).

    Raises `AssemblySourceError` if the scenes cannot be assembled as they
    currently stand (missing production, or any scene with no completed
    video asset AND/OR no completed voiceover asset) - nothing is attempted
    in that case. A downstream failure (source download / ffmpeg / output
    upload / caption segmentation) is NOT raised: it is recorded as a failed
    assembly row and returned in the outcome, mirroring `research.assembly`
    (V1)."""
    production = repository.get_production(production_id)
    if production is None:
        raise AssemblySourceError(f"Production {production_id} does not exist")

    all_scene_numbers = sorted(s["number"] for s in (production.row.get("scenes") or []))
    if not all_scene_numbers:
        raise AssemblySourceError(f"Production {production_id} has no scenes")

    videos = {v.scene_number: v for v in repository.get_completed_video_assets(production_id)}
    voices = {v.scene_number: v for v in repository.get_completed_voice_assets(production_id)}
    missing_video = [n for n in all_scene_numbers if n not in videos]
    missing_voice = [n for n in all_scene_numbers if n not in voices]
    if missing_video or missing_voice:
        parts = []
        if missing_video:
            parts.append(
                f"missing video: {missing_video} - generate video first "
                f"(python -m research.media --production-id {production_id} --media-type video)"
            )
        if missing_voice:
            parts.append(
                f"missing voiceover: {missing_voice} - generate voice first "
                f"(python -m research.voice --production-id {production_id})"
            )
        raise AssemblySourceError(
            f"production has scenes {all_scene_numbers}; " + "; ".join(parts)
        )

    ordered_video = [videos[n] for n in all_scene_numbers]
    ordered_voice = [voices[n] for n in all_scene_numbers]
    video_ids = tuple(v.id for v in ordered_video)
    voice_ids = tuple(v.id for v in ordered_voice)
    source_duration_seconds = (
        sum(v.duration_seconds for v in ordered_video)  # type: ignore[misc]
        if all(v.duration_seconds is not None for v in ordered_video)
        else None
    )
    progress(
        f"✓ {len(all_scene_numbers)} scene(s) ready to assemble, in order {all_scene_numbers} "
        f"(video + voiceover both present for every scene)"
    )

    caption_policy = CAPTION_POLICY_BURNED_V1 if captions else None
    run_key = compute_assembly_run_key(
        production_id=production_id, source_asset_ids=video_ids, source_voice_asset_ids=voice_ids,
        audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER, caption_policy=caption_policy,
    )
    existing_id = repository.find_assembly(run_key)
    if existing_id is not None:
        existing = repository.get_assembly(existing_id) or {}
        progress(f"✓ An identical assembly already exists: {existing_id}")
        existing_captions = existing.get("caption_manifest")
        return AssemblyOutcomeV2(
            status="exists", assembly_id=existing_id, run_key=run_key, source_scene_count=len(all_scene_numbers),
            source_duration_seconds=source_duration_seconds,
            output_duration_seconds=existing.get("output_duration_seconds"),
            output_url=existing.get("output_storage_url"),
            scene_manifest=tuple(existing.get("scene_manifest") or ()),
            caption_manifest=tuple(existing_captions) if existing_captions is not None else None,
        )

    scene_text_by_number = {s["number"]: s["voiceover"] for s in (production.row.get("scenes") or [])}

    try:
        progress("… downloading source video + voiceover clips from storage")
        scenes = [
            SceneMediaInput(
                scene_number=n, video_bytes=storage.download(videos[n].storage_path),
                voice_bytes=storage.download(voices[n].storage_path),
            )
            for n in all_scene_numbers
        ]
        progress("… running ffmpeg (synchronise voiceover + concatenate)")
        output = assembler.assemble_with_voiceover(scenes)
        manifest = _scene_manifest(output.scene_results, video_ids=video_ids, voice_ids=voice_ids)

        caption_manifest: tuple[dict, ...] | None = None
        final_data, final_mime_type, final_duration = output.data, output.mime_type, output.output_duration_seconds
        if captions:
            progress("… segmenting captions from each scene's own voiceover text")
            caption_inputs = [
                SceneCaptionInput(
                    scene_number=r.scene_number, text=scene_text_by_number[r.scene_number],
                    duration_seconds=r.scene_duration_seconds,
                )
                for r in output.scene_results
            ]
            timeline = build_caption_timeline(caption_inputs)
            caption_manifest = tuple(caption_manifest_payload(timeline))
            cues = [CaptionCue(text=s.text, start_seconds=s.start_seconds, end_seconds=s.end_seconds) for s in timeline]
            progress(f"… burning {len(cues)} caption segment(s) into the video")
            captioned = assembler.burn_captions(
                output.data, cues, width=output.metadata["width"], height=output.metadata["height"]
            )
            final_data, final_mime_type, final_duration = (
                captioned.data, captioned.mime_type, captioned.output_duration_seconds,
            )

        path = assembly_output_path(
            production_id=production_id, run_key=run_key, extension=extension_for_mime_type(final_mime_type)
        )
        progress("… uploading assembled video")
        stored = storage.upload(path, final_data, content_type=final_mime_type)
    except (StorageError, FFmpegError, CaptionError) as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        record = AssemblyRecord(
            status=AssemblyStatus.FAILED, run_key=run_key, source_asset_ids=video_ids,
            source_scene_count=len(all_scene_numbers), source_duration_seconds=source_duration_seconds,
            error_message=error_message, source_voice_asset_ids=voice_ids,
            audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER, caption_policy=caption_policy,
        )
        assembly_id = repository.save_assembly(production_id=production_id, record=record)
        progress(f"✗ Assembly failed: {error_message}")
        return AssemblyOutcomeV2(
            status="failed", assembly_id=assembly_id, run_key=run_key, source_scene_count=len(all_scene_numbers),
            source_duration_seconds=source_duration_seconds, error_message=error_message,
        )

    record = AssemblyRecord(
        status=AssemblyStatus.COMPLETED, run_key=run_key, source_asset_ids=video_ids,
        source_scene_count=len(all_scene_numbers), source_duration_seconds=source_duration_seconds,
        output_duration_seconds=final_duration, output_storage_bucket=stored.bucket,
        output_storage_path=stored.path, output_storage_url=stored.public_url, output_mime_type=final_mime_type,
        source_voice_asset_ids=voice_ids, audio_policy=ASSEMBLY_AUDIO_POLICY_VOICEOVER, scene_manifest=manifest,
        caption_policy=caption_policy, caption_manifest=caption_manifest,
    )
    assembly_id = repository.save_assembly(production_id=production_id, record=record)
    progress(f"✓ Assembly completed: {stored.public_url} ({final_duration:.1f}s)")
    return AssemblyOutcomeV2(
        status="completed", assembly_id=assembly_id, run_key=run_key, source_scene_count=len(all_scene_numbers),
        source_duration_seconds=source_duration_seconds, output_duration_seconds=final_duration,
        output_url=stored.public_url, scene_manifest=manifest, caption_manifest=caption_manifest,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.assembly_v2",
        description=(
            "Assemble one production's completed scene videos, synchronised against their own "
            "completed voiceovers, into a single finished MP4."
        ),
    )
    parser.add_argument("--production-id", required=True, help="id of a stored `productions` row")
    parser.add_argument(
        "--captions", action="store_true",
        help="burn each scene's own exact voiceover text in as captions (Captions V1; default: off)",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    if not (settings.supabase_url and settings.supabase_service_role_key):
        print("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return EXIT_CONFIG
    ffmpeg_path = settings.ffmpeg_path or "ffmpeg"
    ffprobe_path = settings.ffprobe_path or "ffprobe"
    missing = [name for name in (ffmpeg_path, ffprobe_path) if shutil.which(name) is None]
    if missing:
        print(
            f"error: {', '.join(missing)} not found on PATH - {FFMPEG_SETUP_HINT}",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    try:
        repository = AssemblyRepository(SupabaseDatabase(settings.supabase_url, settings.supabase_service_role_key))
        storage = SupabaseStorage(
            settings.supabase_url, settings.supabase_service_role_key,
            bucket=settings.media_storage_bucket or "media-assets",
        )
    except PersistenceConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    assembler = FFmpegAssembler.from_settings(settings)

    title = "Media Assembly V2 (video + voiceover + captions)" if args.captions else "Media Assembly V2 (video + voiceover)"
    print(f"{title}\n────────────────────────────────", flush=True)
    try:
        outcome = run_assembly_v2(
            args.production_id, repository=repository, storage=storage, assembler=assembler,
            progress=lambda line: print(line, flush=True), captions=args.captions,
        )
    except AssemblySourceError as exc:
        print(f"\nCannot assemble: {exc}", file=sys.stderr)
        return EXIT_SOURCE
    except Exception as exc:
        print(f"\nAssembly failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    if outcome.status == "failed":
        print(f"\nAssembly failed: {outcome.error_message}", file=sys.stderr)
        return EXIT_FAILED
    verb = "found (identical assembly already existed)" if outcome.status == "exists" else "completed"
    print(
        f"\nAssembly {verb}: {outcome.assembly_id}\n"
        f"  source: {outcome.source_scene_count} scene(s), "
        f"{outcome.source_duration_seconds if outcome.source_duration_seconds is not None else 'unknown'}s "
        f"(source video duration)\n"
        f"  output: {outcome.output_url}\n"
        f"  output duration: {outcome.output_duration_seconds}s"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
