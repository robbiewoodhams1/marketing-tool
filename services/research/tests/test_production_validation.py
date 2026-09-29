import pytest
from opportunity_fixtures import PRODUCT
from production_fixtures import opportunity_row, product_claims, production_output, scene, two_scenes

from research.production_pack import build_production_pack
from research.production_schema import ProductionOutput, ProductionValidationError
from research.production_validation import validate_production

INSIGHTS = [{"id": "insight-1", "title": "T1", "statement": "S1", "limitations": "L1"}]


def pack(product=PRODUCT):
    return build_production_pack(opportunity=opportunity_row(), research_context=INSIGHTS, product=product)


def check(data, product=PRODUCT):
    out = ProductionOutput.from_dict(data)
    return validate_production(out, pack(product=product), product)


# --- reference resolution ------------------------------------------------------------------


def test_a_valid_production_resolves_refs_to_real_keys():
    v = check(production_output())
    assert v.capability_keys == ("quotes", "reusable_templates")
    assert v.scenes[1].capability_keys == ("reusable_templates",)


def test_a_product_claims_ref_not_in_the_pack_is_rejected():
    with pytest.raises(ProductionValidationError, match="unknown reference 'P9'"):
        check(production_output(product_claims=product_claims(capability_refs=("P9",))))


def test_a_scene_capability_ref_not_in_the_pack_is_rejected():
    with pytest.raises(ProductionValidationError, match="unknown reference 'P9'"):
        check(production_output(scenes=[scene(product_capability_refs=["P9"]), scene(number=2)]))


# --- product truth ---------------------------------------------------------------------------


def test_product_claims_explanation_claiming_an_out_of_scope_capability_is_rejected():
    with pytest.raises(ProductionValidationError, match="product truth"):
        check(production_output(product_claims=product_claims(
            explanation="The video shows TradeFlow's bookkeeping feature tracking expenses.")))


def test_a_scene_voiceover_claiming_an_out_of_scope_capability_is_rejected():
    with pytest.raises(ProductionValidationError, match="product truth"):
        check(production_output(scenes=[
            scene(voiceover="This product automatically handles your accounting for you."),
            scene(number=2)]))


def test_the_hook_claiming_an_out_of_scope_capability_is_rejected():
    with pytest.raises(ProductionValidationError, match="product truth"):
        check(production_output(hook="Tired of doing your own accounting by hand?"))


def test_the_cta_claiming_an_out_of_scope_capability_is_rejected():
    with pytest.raises(ProductionValidationError, match="product truth"):
        check(production_output(cta="Let TradeFlow file your tax return for you today."))


def test_on_screen_text_claiming_an_out_of_scope_capability_is_rejected():
    with pytest.raises(ProductionValidationError, match="product truth"):
        check(production_output(scenes=[
            scene(on_screen_text="Automatic accounting"), scene(number=2)]))


def test_out_of_scope_matching_is_case_insensitive():
    with pytest.raises(ProductionValidationError, match="product truth"):
        check(production_output(caption="Full BOOKKEEPING support, built right in."))


@pytest.mark.parametrize("disclaimer", [
    "No bookkeeping or accounting features are shown or claimed in this video.",
    "This does not do tax return filing - only the quote/invoice workflow is depicted.",
    "The earnings overview beat is a light payoff, not a claim about accounting, which the product does not have.",
])
def test_a_negated_disclaimer_about_an_out_of_scope_capability_is_allowed(disclaimer):
    # Discovered via a real smoke-test run: the prompt explicitly asks the model
    # to name, in limitations, anything that could be misread as an unsupported
    # claim. A naive substring match would reject the model for writing exactly
    # the honest disclaimer it was asked for.
    check(production_output(limitations=disclaimer))


def test_a_negation_far_before_the_phrase_does_not_excuse_a_real_claim():
    # The negation must be near the phrase, not merely present anywhere earlier
    # in the same field.
    far = "Not every trade uses every field. " + "Padding text keeps the two apart. " * 3
    with pytest.raises(ProductionValidationError, match="product truth"):
        check(production_output(limitations=far + "TradeFlow handles your accounting for you."))


def test_one_negated_mention_does_not_excuse_a_distant_unnegated_one():
    # A known, documented limitation: the negation window is 60 characters, so
    # a negation can still "bleed" into a second mention of the same phrase
    # that is close by (within the window). Separated further apart, as here,
    # each mention is judged independently.
    padding = "Padding keeps the two mentions further apart than the negation window. " * 2
    with pytest.raises(ProductionValidationError, match="product truth"):
        check(production_output(limitations=(
            "This does not do accounting in scene 1. " + padding +
            "The app also handles your accounting automatically in the background."
        )))


def test_visual_direction_may_neutrally_show_the_products_real_screens():
    # Real capabilities may be shown freely; only out-of-scope claims are rejected.
    v = check(production_output(scenes=[
        scene(visual_direction="Screen recording of the quotes screen with a saved template selected."),
        scene(number=2)]))
    assert v.scenes[0].scene.number == 1


# --- scenes / duration -------------------------------------------------------------------------


def test_scene_duration_outside_the_sane_range_is_rejected():
    with pytest.raises(ProductionValidationError, match="sane per-scene range"):
        check(production_output(scenes=[scene(duration_seconds=25), scene(number=2, duration_seconds=25)],
                                 estimated_duration_seconds=50))


def test_total_duration_outside_the_v1_range_is_rejected():
    with pytest.raises(ProductionValidationError, match="V1 short-form range"):
        check(production_output(estimated_duration_seconds=10,
                                 scenes=[scene(duration_seconds=5), scene(number=2, duration_seconds=5)]))


def test_scene_durations_must_sum_to_roughly_the_stated_total():
    with pytest.raises(ProductionValidationError, match="does not match"):
        check(production_output(estimated_duration_seconds=40,
                                 scenes=[scene(duration_seconds=8), scene(number=2, duration_seconds=10)]))


def test_a_small_tolerance_between_scene_sum_and_stated_total_is_allowed():
    # scenes sum to 18, stated total is 20: within the 3s tolerance
    v = check(production_output(estimated_duration_seconds=20))
    assert v.output.estimated_duration_seconds == 20


# --- length backstop (no unrelated finished asset) -----------------------------------------------


def test_an_overlong_caption_is_rejected():
    with pytest.raises(ProductionValidationError, match="unrelated finished asset"):
        check(production_output(caption="This product solves everything. " * 30))


def test_an_overlong_voiceover_is_rejected():
    with pytest.raises(ProductionValidationError, match="unrelated finished asset"):
        check(production_output(scenes=[
            scene(voiceover="Set it up once and reuse it for every job you do. " * 20),
            scene(number=2)]))


def test_an_overlong_limitations_is_rejected():
    with pytest.raises(ProductionValidationError, match="unrelated finished asset"):
        check(production_output(limitations="This is a limitation. " * 60))


# --- a script IS allowed (unlike an Opportunity brief) --------------------------------------------


def test_a_complete_concrete_script_is_accepted_not_flagged_as_finished_content():
    # The standard fixture already has real voiceover lines for every scene, a
    # real hook and a real cta - this must NOT be rejected as "finished content".
    v = check(production_output(scenes=two_scenes()))
    assert len(v.scenes) == 2
