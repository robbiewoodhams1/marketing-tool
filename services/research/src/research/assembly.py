"""Terminal entry point and service: assemble one persisted Production's
completed scene VIDEO assets into a single finished MP4.

    python -m research.assembly --production-id PRODUCTION_ID

    load production -> load its completed video assets (the CURRENT
    completed asset for each scene number, across any media run) -> require
    every scene to have one -> download each clip from storage -> normalise
    + concatenate via ffmpeg -> upload the result -> persist (success or
    failure) -> report

Assembly is a separate, mechanical stage, downstream of Media Generation:

    Production        -> WHAT should exist: the scenes and their order
    Media Generation   -> individual generated assets, one provider call each
    Assembly (here)    -> combines already-generated assets into one
                          finished file. It never generates new media and
                          never re-decides scene content (see
                          research.media / research.media_pack for that) -
                          scene generation logic does not belong here, and
                          nothing here belongs in Media Generation.

V1 scope, deliberately: picture-only concatenation of already-completed
VIDEO scene assets, in production scene order. No voiceover, captions,
subtitles, music, sound effects, transitions beyond what reliable
concatenation itself requires (see research.ffmpeg_engine), distribution or
publishing - those are separate future chapters. Assembly makes NO AI
provider calls of its own; it only reads what Media Generation already
produced, so it has no per-call generation cost of its own (see
`AssemblyOutcome` - the dashboard shows this as £0/no external cost rather
than inventing one).

Needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, plus a working
`ffmpeg`/`ffprobe` on PATH (or FFMPEG_PATH/FFPROBE_PATH pointing at them).
If ffmpeg is not installed, this fails with a clear, actionable
configuration error rather than a bare "file not found" (see
`research.ffmpeg_engine.FFmpegNotFoundError`).

Exit codes: 0 assembled now, or an identical assembly already existed; 1 the
production/its scenes cannot be assembled as they currently stand (missing
production, or one or more scenes with no completed video asset - generate
video for them first via `python -m research.media --media-type video`);
2 bad arguments / missing configuration; 3 assembly was attempted and failed
(source download / ffmpeg / output upload error).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from research.assembly_schema import AssemblyRecord, AssemblyStatus
from research.assembly_store import AssemblyRepository, compute_assembly_run_key
from research.config import Settings
from research.ffmpeg_engine import FFmpegAssembler, FFmpegError
from research.logging import configure_logging, get_logger
from research.media_storage import (
    StorageError,
    SupabaseStorage,
    assembly_output_path,
    extension_for_mime_type,
)
from research.persistence import PersistenceConfigError, SupabaseDatabase

log = get_logger("research.assembly")

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
class AssemblyOutcome:
    status: str  # "completed" | "exists" | "failed"
    assembly_id: str
    run_key: str
    source_scene_count: int
    source_duration_seconds: float | None
    output_duration_seconds: float | None = None
    output_url: str | None = None
    error_message: str | None = None


def run_assembly(
    production_id: str,
    *,
    repository: AssemblyRepository,
    storage: SupabaseStorage,
    assembler: FFmpegAssembler,
    progress: Progress = _ignore,
) -> AssemblyOutcome:
    """Assemble `production_id`'s completed scene videos into one MP4.

    Raises `AssemblySourceError` if the scenes cannot be assembled as they
    currently stand (missing production, or any scene with no completed
    video asset) - nothing is attempted in that case. A downstream failure
    (source download / ffmpeg / output upload) is NOT raised: it is recorded
    as a failed assembly row and returned in the outcome, mirroring
    `research.media`'s own failure handling."""
    production = repository.get_production(production_id)
    if production is None:
        raise AssemblySourceError(f"Production {production_id} does not exist")

    all_scene_numbers = sorted(s["number"] for s in (production.row.get("scenes") or []))
    if not all_scene_numbers:
        raise AssemblySourceError(f"Production {production_id} has no scenes")

    videos = repository.get_completed_video_assets(production_id)
    by_scene = {v.scene_number: v for v in videos}
    missing = [n for n in all_scene_numbers if n not in by_scene]
    if missing:
        raise AssemblySourceError(
            f"scene(s) {missing} have no completed video asset (production has scenes "
            f"{all_scene_numbers}) - generate video for them first "
            f"(python -m research.media --production-id {production_id} --media-type video)"
        )

    ordered = [by_scene[n] for n in all_scene_numbers]
    source_asset_ids = tuple(v.id for v in ordered)
    source_duration_seconds = (
        sum(v.duration_seconds for v in ordered)  # type: ignore[misc]
        if all(v.duration_seconds is not None for v in ordered)
        else None
    )
    progress(
        f"✓ {len(ordered)} scene(s) ready to assemble, in order {[v.scene_number for v in ordered]} "
        f"(source duration: {source_duration_seconds if source_duration_seconds is not None else 'unknown'}s)"
    )

    run_key = compute_assembly_run_key(production_id=production_id, source_asset_ids=source_asset_ids)
    existing_id = repository.find_assembly(run_key)
    if existing_id is not None:
        existing = repository.get_assembly(existing_id) or {}
        progress(f"✓ An identical assembly already exists: {existing_id}")
        return AssemblyOutcome(
            status="exists", assembly_id=existing_id, run_key=run_key, source_scene_count=len(ordered),
            source_duration_seconds=source_duration_seconds,
            output_duration_seconds=existing.get("output_duration_seconds"),
            output_url=existing.get("output_storage_url"),
        )

    try:
        progress("… downloading source clips from storage")
        clips = [storage.download(v.storage_path) for v in ordered]
        progress("… running ffmpeg (normalise + concatenate)")
        output = assembler.assemble(clips)
        path = assembly_output_path(
            production_id=production_id, run_key=run_key, extension=extension_for_mime_type(output.mime_type)
        )
        progress("… uploading assembled video")
        stored = storage.upload(path, output.data, content_type=output.mime_type)
    except (StorageError, FFmpegError) as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        record = AssemblyRecord(
            status=AssemblyStatus.FAILED, run_key=run_key, source_asset_ids=source_asset_ids,
            source_scene_count=len(ordered), source_duration_seconds=source_duration_seconds,
            error_message=error_message,
        )
        assembly_id = repository.save_assembly(production_id=production_id, record=record)
        progress(f"✗ Assembly failed: {error_message}")
        return AssemblyOutcome(
            status="failed", assembly_id=assembly_id, run_key=run_key, source_scene_count=len(ordered),
            source_duration_seconds=source_duration_seconds, error_message=error_message,
        )

    record = AssemblyRecord(
        status=AssemblyStatus.COMPLETED, run_key=run_key, source_asset_ids=source_asset_ids,
        source_scene_count=len(ordered), source_duration_seconds=source_duration_seconds,
        output_duration_seconds=output.output_duration_seconds, output_storage_bucket=stored.bucket,
        output_storage_path=stored.path, output_storage_url=stored.public_url, output_mime_type=output.mime_type,
    )
    assembly_id = repository.save_assembly(production_id=production_id, record=record)
    progress(f"✓ Assembly completed: {stored.public_url} ({output.output_duration_seconds:.1f}s)")
    return AssemblyOutcome(
        status="completed", assembly_id=assembly_id, run_key=run_key, source_scene_count=len(ordered),
        source_duration_seconds=source_duration_seconds, output_duration_seconds=output.output_duration_seconds,
        output_url=stored.public_url,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.assembly",
        description="Assemble one production's completed scene videos into a single finished MP4.",
    )
    parser.add_argument("--production-id", required=True, help="id of a stored `productions` row")
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

    print("Media Assembly\n────────────────────────────────", flush=True)
    try:
        outcome = run_assembly(
            args.production_id, repository=repository, storage=storage, assembler=assembler,
            progress=lambda line: print(line, flush=True),
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
        f"{outcome.source_duration_seconds if outcome.source_duration_seconds is not None else 'unknown'}s\n"
        f"  output: {outcome.output_url}\n"
        f"  output duration: {outcome.output_duration_seconds}s"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
