"""Deterministic scene generation pack: exactly what an image/video provider
is asked to render, built from one Production scene and a product profile.

Pure: no I/O, no provider call, no database. Deliberately NOT an LLM call and
NOT another creative-reasoning layer: the prompt is plain string assembly from
fields Production already decided and validated (`scene["purpose"]`,
`scene["visual_direction"]`, `scene["on_screen_text"]`,
`scene["product_capability_refs"]`). Media Generation executes the
specification; it does not reinterpret it.

`scene["product_capability_refs"]` here are already resolved capability KEYS
(e.g. "reusable_templates"), not opaque refs: Production persists them
resolved (see `research.production_store.build_save_payload`), so there is
nothing left to look up against the product profile beyond descriptions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from research.media_direction import MediaDirection
from research.media_schema import AssetType
from research.product_context import ProductProfile

MEDIA_PACK_VERSION = "media-pack-v1"

# Every current short-form platform targets vertical video; kept as a lookup
# (not a literal "if platform == tiktok") so a future platform/aspect ratio is
# one more entry, never a code change (Production V1's Part 12 requirement).
PLATFORM_ASPECT_RATIOS: dict[str, str] = {
    "tiktok": "9:16",
    "instagram_reels": "9:16",
    "youtube_shorts": "9:16",
    "generic_short_form": "9:16",
}
DEFAULT_ASPECT_RATIO = "9:16"


def aspect_ratio_for_platform(platform: str | None) -> str:
    return PLATFORM_ASPECT_RATIOS.get(platform or "", DEFAULT_ASPECT_RATIO)


@dataclass(frozen=True)
class MediaScenePack:
    prompt: str  # the exact text handed to the provider
    aspect_ratio: str
    capability_keys: tuple[str, ...]  # already-resolved keys, straight from the scene
    manifest: dict[str, Any]  # what was selected; stored with the asset


def build_scene_prompt(
    *,
    production: Mapping[str, Any],
    scene: Mapping[str, Any],
    product: ProductProfile,
    asset_type: AssetType = AssetType.IMAGE,
    direction: MediaDirection | None = None,
) -> str:
    """Plain text assembly, in three layers, in this order:

    1. Production scene requirements: the scene's own purpose/visual
       direction/on-screen text, the real capabilities this scene is allowed
       to show (with their real descriptions), and an explicit negative list
       drawn from the product's own `out_of_scope`. Never the marketing
       strategy, the CTA, the evidence, or the opportunity - those decisions
       were already made upstream, and never the voiceover: Media Generation
       executes the visual, it does not narrate.
    2. Media Direction (see `research.media_direction`): optional,
       human-edited HOW-to-depict-it guidance. Advisory only - it can never
       add a product capability beyond what layer 1 already allows
       (`research.media_validation` enforces this regardless of what
       direction text says).
    3. Provider-specific requirements: style/negative-prompt boilerplate the
       provider needs, independent of any human input.

    For video (Media V2), on-screen text is deliberately NOT asked of the
    provider: burning text into the generated clip would conflict with a
    future deterministic caption/overlay system, and Veo's text rendering is
    not reliable enough to trust for anything Production actually wrote."""
    if asset_type is AssetType.VIDEO:
        duration = scene.get("duration_seconds")
        lines = [
            f"Short video clip (approximately {duration}s) for one scene of a short-form video "
            f"for the product {product.name}.",
            f"Scene purpose: {scene.get('purpose')}",
            f"Visual direction: {scene.get('visual_direction')}",
        ]
    else:
        lines = [
            f"Still-frame visual for one scene of a short-form video for the product {product.name}.",
            f"Scene purpose: {scene.get('purpose')}",
            f"Visual direction: {scene.get('visual_direction')}",
        ]
        if scene.get("on_screen_text"):
            lines.append(f'On-screen text to render legibly in the shot: "{scene["on_screen_text"]}"')

    capability_keys = tuple(scene.get("product_capability_refs") or [])
    capability_lines = [
        f"- {cap.name}: {cap.description}"
        for key in capability_keys
        if (cap := product.capability(key)) is not None
    ]
    if capability_lines:
        lines.append("Depict only these real product capabilities, nothing else:")
        lines.extend(capability_lines)
    else:
        lines.append("This shot does not need to depict a specific product capability.")

    if product.out_of_scope:
        lines.append(
            "Do NOT depict, imply, or add on-screen text about any of the following, which "
            f"{product.name} does not do: " + ", ".join(product.out_of_scope) + "."
        )

    # Layer 2: Media Direction - optional, human-edited, advisory only.
    if direction is not None and not direction.is_empty():
        lines.append(
            "Human-directed visual guidance (describes HOW to depict the above; it does not add "
            "or remove any product capability):"
        )
        lines.extend(direction.to_prompt_lines())

    # Layer 3: provider-specific requirements.
    if asset_type is AssetType.VIDEO:
        lines.append(
            "Style: clean, modern, authentic mobile-first short-form video look; natural motion; "
            "no fabricated logos or UI beyond what is described above. Do not render any on-screen "
            "text, captions or subtitles into the video - captions are added separately afterwards."
        )
    else:
        lines.append(
            "Style: clean, modern, authentic mobile-first short-form video look. No fabricated "
            "logos, brand names, or UI beyond what is described above."
        )
    return "\n".join(lines)


def build_media_scene_pack(
    *,
    production: Mapping[str, Any],
    scene: Mapping[str, Any],
    product: ProductProfile,
    asset_type: AssetType = AssetType.IMAGE,
    direction: MediaDirection | None = None,
) -> MediaScenePack:
    aspect_ratio = aspect_ratio_for_platform(production.get("platform"))
    prompt = build_scene_prompt(
        production=production, scene=scene, product=product, asset_type=asset_type, direction=direction
    )
    capability_keys = tuple(scene.get("product_capability_refs") or [])
    manifest = {
        "pack_version": MEDIA_PACK_VERSION,
        "production_id": str(production["id"]),
        "scene_number": scene["number"],
        "asset_type": asset_type.value,
        "aspect_ratio": aspect_ratio,
        "platform": production.get("platform"),
        "media_direction_applied": direction is not None and not direction.is_empty(),
        "product_key": product.key,
        "product_version": product.version,
        "capability_keys": list(capability_keys),
    }
    return MediaScenePack(
        prompt=prompt, aspect_ratio=aspect_ratio, capability_keys=capability_keys, manifest=manifest
    )
