"""Terminal entry point and service: generate real media assets from one
persisted Production's scenes.

    python -m research.media --production-id PRODUCTION_ID [--scene N] [--media-type image|video]

    load production -> load its product profile -> create/find the media run
    -> for each requested scene not already completed: build a focused scene
    prompt -> call the image/video provider -> upload the result to storage ->
    validate -> persist (success or failure, one row per attempt) -> report

Media Generation is a separate, mechanical execution stage, downstream of
Production. It never reinterprets the creative brief and never touches the
production it reads from. Unlike Synthesis/Opportunity/Production, a single
invocation can legitimately produce PARTIAL results: each scene is an
independent provider call, so one scene failing does not discard the others
(see `research.media_store`'s module docstring).

The final prompt combines three layers, in order: the Production scene's own
requirements, then any saved `MediaDirection` for this production (see
`research.media_direction` - human-editable HOW-to-depict-it guidance,
reused across regenerations, never a source of new capability claims), then
provider-specific requirements. Editing Media Direction changes the run's
identity (see `research.media_store.compute_run_key`), so a regeneration
after an edit is never silently skipped as already-completed.

Each generated asset carries the provider's own reported generation cost
(`cost_amount`/`cost_currency`; both `None` when the provider has no honest
figure - never invented here or in the provider). A cost already incurred
before a later step fails (e.g. the provider succeeded but storage upload
failed) is still recorded on the failed asset row.

`--media-type` defaults to "image" and is never inferred from configuration:
setting MEDIA_VIDEO_PROVIDER/MEDIA_VIDEO_MODEL in the environment has no
effect unless `--media-type video` is passed explicitly, so it is not
possible to accidentally spend video-generation credits when an image was
intended. A run is for exactly one media type; its identity (run_key) reflects
only that type's provider/model, never the other type's unrelated env vars.

Needs SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and GEMINI_API_KEY
(MEDIA_IMAGE_PROVIDER/MEDIA_IMAGE_MODEL optional, only "gemini" implemented;
MEDIA_VIDEO_PROVIDER/MEDIA_VIDEO_MODEL optional, only "veo" implemented).
Exit codes: 0 at least one requested scene is now completed (whether
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
from research.media_direction import MediaDirection
from research.media_pack import MEDIA_PACK_VERSION, build_media_scene_pack
from research.media_providers import (
    ImageProvider,
    MediaProviderConfigError,
    MediaProviderError,
    VideoProvider,
)
from research.media_schema import AssetStatus, AssetType, GeneratedAsset
from research.media_storage import StorageError, SupabaseStorage, extension_for_mime_type, scene_asset_path
from research.media_store import MediaRepository, build_run_payload, compute_run_key
from research.media_validation import validate_asset
from research.persistence import PersistenceConfigError, SupabaseDatabase
from research.products import UnknownProductError, get_product_profile

log = get_logger("research.media")

Progress = Callable[[str], None]

EXIT_OK, EXIT_SOURCE, EXIT_CONFIG, EXIT_FAILED = 0, 1, 2, 3

# The one provider implemented for each media type in V2. A different
# --media-type value simply does not exist yet; MEDIA_IMAGE_PROVIDER /
# MEDIA_VIDEO_PROVIDER are read so a future second provider needs no CLI
# change, but any other value is refused.
IMAGE_PROVIDER_NAME = "gemini"
VIDEO_PROVIDER_NAME = "veo"


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
    storage: SupabaseStorage,
    media_type: AssetType = AssetType.IMAGE,
    image_provider: ImageProvider | None = None,
    video_provider: VideoProvider | None = None,
    scenes: Sequence[int] | None = None,
    progress: Progress = _ignore,
) -> MediaRunOutcome:
    """Generate media for `production_id`'s scenes (all of them, or only
    `scenes` if given), of exactly one `media_type`. Raises `MediaSourceError`
    if the production/product cannot be resolved; otherwise never raises for
    a single scene's generation failure - that scene is recorded as failed
    (including a provider timeout) and generation continues (see the module
    docstring). Exactly one of `image_provider`/`video_provider` must be
    given, matching `media_type`."""
    if media_type is AssetType.IMAGE:
        if image_provider is None:
            raise ValueError("image_provider is required when media_type is IMAGE")
        provider_name, model = IMAGE_PROVIDER_NAME, image_provider.model
    else:
        if video_provider is None:
            raise ValueError("video_provider is required when media_type is VIDEO")
        provider_name, model = VIDEO_PROVIDER_NAME, video_provider.model

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
    progress(f"✓ Production loaded: {len(all_scenes)} scene(s), {len(requested)} requested ({media_type.value})")

    direction = MediaDirection.from_row(repository.get_media_direction(production_id))
    direction_snapshot = direction.to_snapshot()
    progress(
        "✓ Media direction: applied ({} field(s))".format(len(direction_snapshot))
        if direction_snapshot else "✓ Media direction: none set"
    )

    # A run's identity reflects ONLY the active modality's provider/model -
    # never the other modality's unrelated configuration (see module
    # docstring) - plus the media direction actually in effect, so editing
    # direction and regenerating creates a new run rather than being silently
    # skipped as already-completed.
    run_key = compute_run_key(
        production_id=production_id,
        image_provider=provider_name if media_type is AssetType.IMAGE else None,
        image_model=model if media_type is AssetType.IMAGE else None,
        video_provider=provider_name if media_type is AssetType.VIDEO else None,
        video_model=model if media_type is AssetType.VIDEO else None,
        pack_version=MEDIA_PACK_VERSION,
        media_direction=direction_snapshot,
    )
    existing_run_id = repository.find_run(run_key)
    if existing_run_id is not None:
        run_id = existing_run_id
    else:
        saved = repository.save_run(
            build_run_payload(
                production_id=production_id,
                image_provider=provider_name if media_type is AssetType.IMAGE else None,
                image_model=model if media_type is AssetType.IMAGE else None,
                video_provider=provider_name if media_type is AssetType.VIDEO else None,
                video_model=model if media_type is AssetType.VIDEO else None,
                run_key=run_key, scene_count=len(all_scenes),
                manifest={"pack_version": MEDIA_PACK_VERSION, "production_id": production_id},
                pack_version=MEDIA_PACK_VERSION,
                media_direction=direction_snapshot,
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
        pack = build_media_scene_pack(
            production=production.row, scene=scene, product=product, asset_type=media_type, direction=direction
        )
        # Set as soon as (and only if) the provider call itself succeeds, so a
        # LATER failure (storage upload, validation) still records the real
        # cost already incurred - never invented, never silently dropped.
        cost_amount: float | None = None
        cost_currency: str | None = None
        try:
            duration_seconds: float | None = None
            if media_type is AssetType.IMAGE:
                image = image_provider.generate_image(pack.prompt, aspect_ratio=pack.aspect_ratio)  # type: ignore[union-attr]
                mime_type, raw_data = image.mime_type, image.data
                width, height = image.width, image.height
                provider_metadata = dict(image.metadata)
                cost_amount, cost_currency = image.cost_amount, image.cost_currency
            else:
                video = video_provider.generate_video(  # type: ignore[union-attr]
                    pack.prompt, aspect_ratio=pack.aspect_ratio, duration_seconds=scene.get("duration_seconds"),
                )
                mime_type, raw_data = video.mime_type, video.data
                width, height = video.width, video.height
                duration_seconds = video.duration_seconds
                provider_metadata = dict(video.metadata)
                cost_amount, cost_currency = video.cost_amount, video.cost_currency

            extension = extension_for_mime_type(mime_type)
            path = scene_asset_path(
                production_id=production_id, media_run_id=run_id, scene_number=number, extension=extension
            )
            stored = storage.upload(path, raw_data, content_type=mime_type)
            asset = GeneratedAsset(
                scene_number=number, asset_type=media_type, status=AssetStatus.COMPLETED,
                provider=provider_name, model=model, prompt=pack.prompt,
                generation_config={"aspect_ratio": pack.aspect_ratio}, product_capability_refs=pack.capability_keys,
                provider_metadata=provider_metadata, storage_bucket=stored.bucket, storage_path=stored.path,
                storage_url=stored.public_url, mime_type=mime_type, width=width, height=height,
                duration_seconds=duration_seconds, cost_amount=cost_amount, cost_currency=cost_currency,
            )
            validate_asset(asset, production=production.row)
            repository.save_asset(media_run_id=run_id, production_id=production_id, asset=asset)
            completed.append(number)
            progress(f"✓ Scene {number} {media_type.value} generated: {stored.public_url}")
        except (MediaProviderError, StorageError) as exc:
            asset = GeneratedAsset(
                scene_number=number, asset_type=media_type, status=AssetStatus.FAILED,
                provider=provider_name, model=model, prompt=pack.prompt,
                generation_config={"aspect_ratio": pack.aspect_ratio}, product_capability_refs=pack.capability_keys,
                cost_amount=cost_amount, cost_currency=cost_currency,
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
    provider_name = (settings.media_image_provider or IMAGE_PROVIDER_NAME).strip().lower()
    if provider_name != IMAGE_PROVIDER_NAME:
        raise MediaConfigError(f"MEDIA_IMAGE_PROVIDER={provider_name!r} is not implemented; only 'gemini' is")
    from research.gemini import GeminiImageProvider

    return GeminiImageProvider.from_settings(settings)


def _resolve_video_provider(settings: Settings) -> VideoProvider:
    provider_name = (settings.media_video_provider or VIDEO_PROVIDER_NAME).strip().lower()
    if provider_name != VIDEO_PROVIDER_NAME:
        raise MediaConfigError(f"MEDIA_VIDEO_PROVIDER={provider_name!r} is not implemented; only 'veo' is")
    from research.veo import VeoVideoProvider

    return VeoVideoProvider.from_settings(settings)


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
    parser.add_argument(
        "--media-type", choices=[t.value for t in AssetType], default=AssetType.IMAGE.value,
        help="which kind of asset to generate (default: image). Video is never used unless "
        "explicitly requested here, regardless of any MEDIA_VIDEO_* configuration.",
    )
    args = parser.parse_args(argv)
    media_type = AssetType(args.media_type)

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    if not (settings.supabase_url and settings.supabase_service_role_key):
        print("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return EXIT_CONFIG
    try:
        image_provider = _resolve_image_provider(settings) if media_type is AssetType.IMAGE else None
        video_provider = _resolve_video_provider(settings) if media_type is AssetType.VIDEO else None
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

    print("Media Generation\n────────────────────────────────", flush=True)
    try:
        outcome = run_media_generation(
            args.production_id, repository=repository, storage=storage, media_type=media_type,
            image_provider=image_provider, video_provider=video_provider, scenes=args.scenes,
            progress=lambda line: print(line, flush=True),
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
