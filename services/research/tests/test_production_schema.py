import pytest
from production_fixtures import GOOD_OUTPUT, product_claims, production_output, scene, two_scenes

from research.production_schema import (
    MAX_HASHTAGS,
    MAX_SCENES,
    MIN_SCENES,
    Platform,
    ProductionOutput,
    ProductionSchemaError,
)
from research.production_prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
    production_output_json_schema,
)


def parse(data):
    return ProductionOutput.from_dict(data)


# --- valid --------------------------------------------------------------------------------


def test_valid_output_parses_and_roundtrips():
    out = parse(GOOD_OUTPUT)
    assert out.platform is Platform.TIKTOK
    assert out.estimated_duration_seconds == 18
    assert len(out.scenes) == 2 and out.scenes[0].number == 1 and out.scenes[1].number == 2
    assert out.product_claims.capability_refs == ("P1", "P2")
    assert out.to_dict() == GOOD_OUTPUT  # nothing added, nothing lost


def test_platform_taxonomy_is_small_and_as_specified():
    assert [p.value for p in Platform] == [
        "tiktok", "instagram_reels", "youtube_shorts", "generic_short_form"]


def test_optional_scene_fields_may_be_null():
    out = parse(production_output(scenes=[scene(on_screen_text=None, transition=None), scene(number=2)]))
    assert out.scenes[0].on_screen_text is None and out.scenes[0].transition is None


# --- malformed top level --------------------------------------------------------------------


@pytest.mark.parametrize("data", [None, [], "text", 3, {}])
def test_missing_or_non_object_output_is_rejected(data):
    with pytest.raises(ProductionSchemaError):
        parse(data)


def test_unknown_top_level_keys_are_rejected():
    with pytest.raises(ProductionSchemaError, match="unexpected"):
        parse({**GOOD_OUTPUT, "extra": 1})


@pytest.mark.parametrize("field", [
    "title", "hook", "platform", "estimated_duration_seconds", "scenes", "cta", "caption",
    "hashtags", "product_claims", "limitations",
])
def test_each_required_field_is_required(field):
    with pytest.raises(ProductionSchemaError, match="missing keys"):
        parse({k: v for k, v in GOOD_OUTPUT.items() if k != field})


@pytest.mark.parametrize("field", ["title", "hook", "cta", "caption", "limitations"])
def test_text_fields_must_not_be_blank(field):
    with pytest.raises(ProductionSchemaError):
        parse(production_output(**{field: "   "}))


def test_platform_must_be_a_known_value():
    with pytest.raises(ProductionSchemaError, match="is not one of"):
        parse(production_output(platform="facebook"))


def test_estimated_duration_must_be_an_integer():
    for bad in (18.5, "18", True, None):
        with pytest.raises(ProductionSchemaError, match="integer"):
            parse(production_output(estimated_duration_seconds=bad))


# --- scenes ---------------------------------------------------------------------------------


def test_scenes_must_be_within_the_min_max_count():
    with pytest.raises(ProductionSchemaError, match=f"between {MIN_SCENES} and {MAX_SCENES}"):
        parse(production_output(scenes=[scene()]))
    with pytest.raises(ProductionSchemaError, match=f"between {MIN_SCENES} and {MAX_SCENES}"):
        parse(production_output(scenes=[scene(number=n) for n in range(1, MAX_SCENES + 2)]))


def test_scene_numbers_must_be_sequential_from_one():
    with pytest.raises(ProductionSchemaError, match="sequential"):
        parse(production_output(scenes=[scene(number=1), scene(number=3)]))
    with pytest.raises(ProductionSchemaError, match="sequential"):
        parse(production_output(scenes=[scene(number=2), scene(number=1)]))


def test_scene_duration_must_be_a_positive_integer():
    with pytest.raises(ProductionSchemaError, match="duration_seconds"):
        parse(production_output(scenes=[scene(duration_seconds=0), scene(number=2)]))
    with pytest.raises(ProductionSchemaError, match="duration_seconds"):
        parse(production_output(scenes=[scene(duration_seconds=-1), scene(number=2)]))


def test_scene_number_must_be_a_positive_integer():
    with pytest.raises(ProductionSchemaError, match="number must be a positive integer"):
        parse(production_output(scenes=[scene(number=0), scene(number=2)]))


@pytest.mark.parametrize("field", ["purpose", "voiceover", "visual_direction"])
def test_required_scene_text_fields_must_not_be_blank(field):
    with pytest.raises(ProductionSchemaError):
        parse(production_output(scenes=[scene(**{field: "  "}), scene(number=2)]))


def test_scene_capability_refs_must_match_the_p_pattern():
    with pytest.raises(ProductionSchemaError, match="malformed capability reference"):
        parse(production_output(scenes=[scene(product_capability_refs=["N1"]), scene(number=2)]))


def test_scene_capability_refs_reject_duplicates():
    with pytest.raises(ProductionSchemaError, match="duplicate"):
        parse(production_output(scenes=[scene(product_capability_refs=["P1", "P1"]), scene(number=2)]))


def test_scene_capability_refs_may_be_empty():
    out = parse(production_output(scenes=[scene(product_capability_refs=[]), scene(number=2)]))
    assert out.scenes[0].product_capability_refs == ()


# --- hashtags ----------------------------------------------------------------------------------


def test_hashtags_enforce_a_maximum():
    with pytest.raises(ProductionSchemaError, match=f"at most {MAX_HASHTAGS}"):
        parse(production_output(hashtags=[f"tag{n}" for n in range(MAX_HASHTAGS + 1)]))


def test_hashtags_reject_duplicates():
    with pytest.raises(ProductionSchemaError, match="duplicate"):
        parse(production_output(hashtags=["tag", "tag"]))


def test_hashtags_may_be_empty():
    out = parse(production_output(hashtags=[]))
    assert out.hashtags == ()


# --- product_claims ------------------------------------------------------------------------------


def test_product_claims_requires_at_least_one_capability_ref():
    with pytest.raises(ProductionSchemaError, match="at least one capability"):
        parse(production_output(product_claims=product_claims(capability_refs=())))


def test_product_claims_capability_ref_must_match_the_p_pattern():
    with pytest.raises(ProductionSchemaError, match="malformed capability reference"):
        parse(production_output(product_claims=product_claims(capability_refs=("N1",))))


def test_product_claims_rejects_duplicate_refs():
    with pytest.raises(ProductionSchemaError, match="duplicate"):
        parse(production_output(product_claims=product_claims(capability_refs=("P1", "P1"))))


def test_product_claims_explanation_must_not_be_blank():
    with pytest.raises(ProductionSchemaError):
        parse(production_output(product_claims=product_claims(explanation="  ")))


# --- prompt / json schema -----------------------------------------------------------------------


def test_prompt_version_is_set():
    assert PROMPT_VERSION == "production-v1"


def test_system_prompt_states_the_core_rules():
    for phrase in ("Product truth", "script", "short-form"):
        assert phrase in SYSTEM_PROMPT


def test_json_schema_is_strict_and_matches_the_dataclass_fields():
    schema = production_output_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "title", "hook", "platform", "estimated_duration_seconds", "scenes", "cta", "caption",
        "hashtags", "product_claims", "limitations",
    }
    scene_schema = schema["properties"]["scenes"]["items"]
    assert scene_schema["additionalProperties"] is False
    assert schema["properties"]["platform"]["enum"] == [p.value for p in Platform]


def test_build_user_prompt_wraps_the_pack_json_losslessly():
    import json as json_module

    from opportunity_fixtures import PRODUCT
    from research.production_pack import build_production_pack

    pack = build_production_pack(
        opportunity={"id": "opp-1", "title": "t", "audience": "a", "problem": "p",
                     "opportunity": "o", "angle": "ang", "marketing_objective": "activation",
                     "suggested_formats": ["tutorial"], "cta_direction": "c", "limitations": "l"},
        research_context=[{"id": "insight-1", "title": "t", "statement": "s", "limitations": "l"}],
        product=PRODUCT,
    )
    prompt = build_user_prompt(pack)
    assert prompt.count("```") == 2
    body = prompt.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    assert json_module.loads(body) == pack.prompt


def test_duplicate_scenes_helper_produces_two_valid_scenes():
    assert [s["number"] for s in two_scenes()] == [1, 2]
