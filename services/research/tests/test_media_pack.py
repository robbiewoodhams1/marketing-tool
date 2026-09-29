from media_fixtures import PRODUCT, production_row, scene_row

from research.media_pack import (
    DEFAULT_ASPECT_RATIO,
    PLATFORM_ASPECT_RATIOS,
    aspect_ratio_for_platform,
    build_media_scene_pack,
    build_scene_prompt,
)
from research.media_schema import AssetType


def test_aspect_ratio_is_looked_up_per_platform_not_hardcoded():
    assert aspect_ratio_for_platform("tiktok") == "9:16"
    assert aspect_ratio_for_platform("instagram_reels") == "9:16"
    assert aspect_ratio_for_platform("unknown_future_platform") == DEFAULT_ASPECT_RATIO
    assert aspect_ratio_for_platform(None) == DEFAULT_ASPECT_RATIO


def test_every_known_production_platform_has_an_aspect_ratio():
    from research.production_schema import Platform

    for platform in Platform:
        assert platform.value in PLATFORM_ASPECT_RATIOS


def test_prompt_includes_scene_fields():
    production = production_row()
    scene = scene_row(1)
    prompt = build_scene_prompt(production=production, scene=scene, product=PRODUCT)
    assert scene["purpose"] in prompt
    assert scene["visual_direction"] in prompt
    assert scene["on_screen_text"] in prompt


def test_prompt_never_mentions_the_cta_evidence_or_opportunity():
    # Media Generation executes the scene; it must not be handed the CTA,
    # research evidence or opportunity brief - those decisions are upstream.
    production = production_row(cta="Build your first template today.", opportunity_id="opp-1")
    scene = scene_row(1)
    prompt = build_scene_prompt(production=production, scene=scene, product=PRODUCT)
    assert "Build your first template today" not in prompt
    assert "opp-1" not in prompt


def test_prompt_only_names_capabilities_the_scene_actually_uses():
    scene = scene_row(1, product_capability_refs=["quotes"])
    prompt = build_scene_prompt(production=production_row(), scene=scene, product=PRODUCT)
    assert "Quotes" in prompt or "quotes" in prompt.lower()
    assert "Reusable templates" not in prompt


def test_a_scene_with_no_capabilities_gets_a_neutral_line_not_an_empty_one():
    scene = scene_row(1, product_capability_refs=[])
    prompt = build_scene_prompt(production=production_row(), scene=scene, product=PRODUCT)
    assert "does not need to depict a specific product capability" in prompt


def test_prompt_lists_out_of_scope_phrases_as_explicit_restrictions():
    prompt = build_scene_prompt(production=production_row(), scene=scene_row(1), product=PRODUCT)
    for phrase in PRODUCT.out_of_scope:
        assert phrase in prompt


def test_an_unknown_capability_ref_is_silently_skipped_not_a_crash():
    # Defence in depth: if a scene ever cited a capability outside the product
    # profile (should be impossible - Production already validated this),
    # the prompt builder must not crash; research.media_validation is what
    # actually rejects this case before persistence.
    scene = scene_row(1, product_capability_refs=["nonexistent_capability"])
    prompt = build_scene_prompt(production=production_row(), scene=scene, product=PRODUCT)
    assert "does not need to depict a specific product capability" in prompt


def test_build_media_scene_pack_carries_capability_keys_and_manifest():
    pack = build_media_scene_pack(
        production=production_row(), scene=scene_row(1, product_capability_refs=["quotes"]), product=PRODUCT,
    )
    assert pack.capability_keys == ("quotes",)
    assert pack.aspect_ratio == "9:16"
    assert pack.manifest["production_id"] == "prod-1"
    assert pack.manifest["scene_number"] == 1
    assert pack.manifest["asset_type"] == "image"
    assert pack.manifest["product_key"] == "testproduct"


def test_build_media_scene_pack_defaults_to_image_asset_type():
    pack = build_media_scene_pack(production=production_row(), scene=scene_row(1), product=PRODUCT)
    assert pack.manifest["asset_type"] == AssetType.IMAGE.value


def test_prompt_is_deterministic_for_the_same_inputs():
    a = build_scene_prompt(production=production_row(), scene=scene_row(1), product=PRODUCT)
    b = build_scene_prompt(production=production_row(), scene=scene_row(1), product=PRODUCT)
    assert a == b
