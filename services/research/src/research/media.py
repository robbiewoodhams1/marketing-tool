"""Terminal entry point and service: generate real media assets from one
persisted Production's scenes.

    python -m research.media --production-id PRODUCTION_ID [--scene N]

    load production -> load its product profile -> create/find the media run
    -> for each requested scene not already completed: build a focused scene
    prompt -> call the image provider -> upload the result to storage ->
    validate -> persist (success or failure, one row per attempt) -> report

Media Generation is a separate, mechanical execution stage, downstream of
Production. It never reinterprets the creative brief and never touches the
production it reads from. Unlike Synthesis/Opportunity/Production, a single
invocation can legitimately produce PARTIAL results: each scene is an
independent provider call, so one scene failing does not discard the others
(see `research.media_store`'s module docstring).

Needs SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and GEMINI_API_KEY
(MEDIA_IMAGE_PROVIDER/MEDIA_IMAGE_MODEL optional; only "gemini" is implemented
in V1). Exit codes: 0 at least one requested scene is now completed (whether
generated now or already done), 1 the production cannot be used, 2 bad
arguments / missing configuration, 3 every requested scene failed and none
were already completed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from research.config import Settings
from research.logging import configure_logging, get_logger
from research.media_pack import MEDIA_PACK_VERSION, build_media_scene_pack
from research.media_providers import ImageProvider, MediaProviderConfigError, MediaProviderError
from research.media_schema import AssetStatus, AssetType, GeneratedAsset
from research.media_storage import StorageError, SupabaseStorage, extension_for_mime_type, scene_asset_path
from research.media_store import MediaRepository, build_run_payload, compute_run_key
from research.media_validation import validate_asset
from research.persistence import PersistenceConfigError, SupabaseDatabase
from research.products import UnknownProductError, get_product_profile

log = get_logger("research.media")

Progress = Callable[[str], None]

EXIT_OK, EXIT_SOURCE, EXIT_CONFIG, EXIT_FAILED = 0, 1, 2, 3


class MediaSourceError(Exception):
    """The requested production cannot be used. Nothing was changed."""


class MediaConfigError(Exception):
    """Media generation is not configured usably (e.g. an unimplemented provider)."""


@dataclass(frozen=True)
class MediaRunOutcome:
    status: str  # "completed" | "partial" | "failed"
    run_id: str
    run_key: str
    requested_scenes: tuple[int, ...]
    completed_scenes: tuple[int, ...] = ()
    already_completed_scenes: tuple[int, ...] = ()
    failed_scenes: dict[int, str] = field(default_factory=dict)


def _ignore(_: str) -> None:
    pass


def run_media_generation(
    production_id: str,
    *,
    repository: MediaRepository,
    image_provider: ImageProvider,
    storage: SupabaseStorage,
    scenes: Sequence[int] | None = None,
    video_provider_name: str | None = None,
    video_model: str | None = None,
    progress: Progress = _ignore,
) -> MediaRunOutcome:
    """Generate media for `production_id`'s scenes (all of them, or only
    `scenes` if given). Raises `MediaSourceError` if the production/product
    cannot be resolved; otherwise never raises for a single scene's
    generation failure - that scene is recorded as failed and generation
    continues (see the module docstring)."""
    production = repository.get_production(production_id)
    if production is None:
        raise MediaSourceError(f"Production {production_id} does not exist")
    try:
        product = get_product_profile(production.product_key)
    except UnknownProductError as exc:
        raise MediaSourceError(str(exc)) from None

    all_scenes = production.row.get("scenes") or []
    by_number = {s["number"]: s for s in all_scenes}
    requested = tuple(scenes) if scenes else tuple(sorted(by_number))
    unknown = [n for n in requested if n not in by_number]
    if unknown:
        raise MediaSourceError(
            f"scene(s) {unknown} do not exist in production {production_id} "
            f"(has scenes {sorted(by_number)})"
        )
    progress(f"✓ Production loaded: {len(all_scenes)} scene(s), {len(requested)} requested")

    run_key = compute_run_key(
        production_id=production_id,
        image_provider="gemini",
        image_model=image_provider.model,
        video_provider=video_provider_name,
        video_model=video_model,
        pack_version=MEDIA_PACK_VERSION,
    )
    existing_run_id = repository.find_run(run_key)
    if existing_run_id is not None:
        run_id = existing_run_id
    else:
        saved = repository.save_run(
            build_run_payload(
                production_id=production_id, image_provider="gemini", image_model=image_provider.model,
                video_provider=video_provider_name, video_model=video_model, run_key=run_key,
                scene_count=len(all_scenes), manifest={"pack_version": MEDIA_PACK_VERSION, "production_id": production_id},
                pack_version=MEDIA_PACK_VERSION,
            )
        )
        run_id = saved.run_id
    progress(f"✓ Media run {'found' if existing_run_id else 'created'}: {run_id}")

    completed: list[int] = []
    already_completed: list[int] = []
    failed: dict[int, str] = {}
    for number in requested:
        if repository.find_completed_asset(run_id, number):
            progress(f"✓ Scene {number} already generated; skipping")
            already_completed.append(number)
            continue

        scene = by_number[number]
        pack = build_media_scene_pack(production=production.row, scene=scene, product=product, asset_type=AssetType.IMAGE)
        try:
            image = image_provider.generate_image(pack.prompt, aspect_ratio=pack.aspect_ratio)
            extension = extension_for_mime_type(image.mime_type)
            path = scene_asset_path(
                production_id=production_id, media_run_id=run_id, scene_number=number, extension=extension
            )
            stored = storage.upload(path, image.data, content_type=image.mime_type)
            asset = GeneratedAsset(
                scene_number=number, asset_type=AssetType.IMAGE, status=AssetStatus.COMPLETED,
                provider="gemini", model=image_provider.model, prompt=pack.prompt,
                generation_config={"aspect_ratio": pack.aspect_ratio}, product_capability_refs=pack.capability_keys,
                provider_metadata=dict(image.metadata), storage_bucket=stored.bucket, storage_path=stored.path,
                storage_url=stored.public_url, mime_type=image.mime_type, width=image.width, height=image.height,
            )
            validate_asset(asset, production=production.row)
            repository.save_asset(media_run_id=run_id, production_id=production_id, asset=asset)
            completed.append(number)
            progress(f"✓ Scene {number} generated: {stored.public_url}")
        except (MediaProviderError, StorageError) as exc:
            asset = GeneratedAsset(
                scene_number=number, asset_type=AssetType.IMAGE, status=AssetStatus.FAILED,
                provider="gemini", model=image_provider.model, prompt=pack.prompt,
                generation_config={"aspect_ratio": pack.aspect_ratio}, product_capability_refs=pack.capability_keys,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            validate_asset(asset, production=production.row)
            repository.save_asset(media_run_id=run_id, production_id=production_id, asset=asset)
            failed[number] = str(exc)
            progress(f"✗ Scene {number} failed: {exc}")

    total_ok = len(completed) + len(already_completed)
    status = "completed" if total_ok == len(requested) else ("partial" if total_ok > 0 else "failed")
    return MediaRunOutcome(
        status=status, run_id=run_id, run_key=run_key, requested_scenes=requested,
        completed_scenes=tuple(completed), already_completed_scenes=tuple(already_completed), failed_scenes=failed,
    )


def _resolve_image_provider(settings: Settings) -> ImageProvider:
    provider_name = (settings.media_image_provider or "gemini").strip().lower()
    if provider_name != "gemini":
        raise MediaConfigError(
            f"MEDIA_IMAGE_PROVIDER={provider_name!r} is not implemented in V1; only 'gemini' is"
        )
    from research.gemini import GeminiImageProvider

    return GeminiImageProvider.from_settings(settings)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.media",
        description="Generate media assets for one production's scenes.",
    )
    parser.add_argument("--production-id", required=True, help="id of a stored `productions` row")
    parser.add_argument(
        "--scene", type=int, action="append", dest="scenes",
        help="generate only this scene number (repeatable); default: every scene",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    if not (settings.supabase_url and settings.supabase_service_role_key):
        print("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return EXIT_CONFIG
    try:
        image_provider = _resolve_image_provider(settings)
    except (MediaConfigError, MediaProviderConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        repository = MediaRepository(
            SupabaseDatabase(settings.supabase_url, settings.supabase_service_role_key)
        )
        storage = SupabaseStorage(
            settings.supabase_url, settings.supabase_service_role_key,
            bucket=settings.media_storage_bucket or "media-assets",
        )
    except PersistenceConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if settings.media_video_provider:
        print(
            f"note: MEDIA_VIDEO_PROVIDER={settings.media_video_provider!r} is set but no video "
            "provider is implemented in V1; only image scenes will be generated.",
        )

    print("Media Generation\n────────────────────────────────", flush=True)
    try:
        outcome = run_media_generation(
            args.production_id, repository=repository, image_provider=image_provider, storage=storage,
            scenes=args.scenes, video_provider_name=settings.media_video_provider,
            video_model=settings.media_video_model, progress=lambda line: print(line, flush=True),
        )
    except MediaSourceError as exc:
        print(f"\nCannot generate media: {exc}", file=sys.stderr)
        return EXIT_SOURCE
    except Exception as exc:
        print(f"\nMedia generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    total_new = len(outcome.completed_scenes)
    total_ok = total_new + len(outcome.already_completed_scenes)
    print(
        f"\nMedia run {outcome.status}: {outcome.run_id} "
        f"({total_ok}/{len(outcome.requested_scenes)} scene(s) complete, {total_new} generated now, "
        f"{len(outcome.failed_scenes)} failed)."
    )
    if outcome.failed_scenes:
        for number, message in outcome.failed_scenes.items():
            print(f"  scene {number}: {message}", file=sys.stderr)
    return EXIT_FAILED if total_ok == 0 else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
