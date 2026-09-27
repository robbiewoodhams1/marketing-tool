import pytest
from opportunity_fixtures import JOB, PRODUCT, insight_row, opportunity, output, product_fit, research_evidence

from research.opportunity import OpportunityCreationOutput, OpportunityValidationError
from research.opportunity_pack import build_opportunity_pack
from research.opportunity_validation import validate_opportunities

SYNTHESIS = {"id": "syn-1", "research_job_id": "job-1"}


def pack(rows=None, product=PRODUCT):
    return build_opportunity_pack(
        job=JOB, synthesis=SYNTHESIS,
        insight_rows=[insight_row(1), insight_row(2)] if rows is None else rows,
        product=product,
    )


def check(op, p=None, product=PRODUCT):
    p = p or pack(product=product)
    out = OpportunityCreationOutput.from_dict(output(op))
    return validate_opportunities(out, p, product)


# --- reference resolution ------------------------------------------------------------------


def test_a_valid_opportunity_resolves_refs_to_real_ids_and_keys():
    [v] = check(opportunity()).opportunities
    assert v.insight_ids == ("insight-1",)
    assert v.capability_keys == ("quotes",)


def test_an_insight_ref_not_in_the_pack_is_rejected():
    with pytest.raises(OpportunityValidationError, match="unknown reference 'N9'"):
        check(opportunity(research_evidence=[research_evidence(ref="N9")]))


def test_a_capability_ref_not_in_the_pack_is_rejected():
    with pytest.raises(OpportunityValidationError, match="unknown reference 'P9'"):
        check(opportunity(product_fit=product_fit(capability_refs=("P9",))))


def test_multiple_insight_refs_all_resolve():
    [v] = check(opportunity(research_evidence=[research_evidence(ref="N1"), research_evidence(ref="N2")])).opportunities
    assert set(v.insight_ids) == {"insight-1", "insight-2"}


def test_multiple_capability_refs_all_resolve():
    [v] = check(opportunity(product_fit=product_fit(capability_refs=("P1", "P2")))).opportunities
    assert set(v.capability_keys) == {"quotes", "reusable_templates"}


# --- product truth ---------------------------------------------------------------------------


def test_product_fit_explanation_claiming_an_out_of_scope_capability_is_rejected():
    with pytest.raises(OpportunityValidationError, match="product truth"):
        check(opportunity(product_fit=product_fit(
            explanation="TradeFlow's bookkeeping features help track every expense.")))


def test_cta_direction_claiming_an_out_of_scope_capability_is_rejected():
    with pytest.raises(OpportunityValidationError, match="product truth"):
        check(opportunity(cta_direction="Tell viewers to use the product's accounting tools to file their tax return."))


def test_the_problem_field_may_legitimately_describe_an_out_of_scope_market_pain():
    # The AUDIENCE's real problem can be about bookkeeping even when the
    # product does not solve it - only claiming the PRODUCT does it is wrong.
    [v] = check(opportunity(problem="Sole traders struggle with bookkeeping and expense tracking.")).opportunities
    assert v.opportunity.problem.startswith("Sole traders")


def test_out_of_scope_matching_is_case_insensitive():
    with pytest.raises(OpportunityValidationError, match="product truth"):
        check(opportunity(product_fit=product_fit(explanation="Great BOOKKEEPING support built in.")))


# --- opportunity vs. finished content ----------------------------------------------------------


def test_an_overlong_title_is_rejected():
    with pytest.raises(OpportunityValidationError, match="finished copy"):
        check(opportunity(title="x" * 200))


def test_an_overlong_field_is_rejected():
    with pytest.raises(OpportunityValidationError, match="finished"):
        check(opportunity(opportunity="A guide about repeat jobs. " * 30))


@pytest.mark.parametrize("marker", [
    "INT. WORKSHOP - DAY", "EXT. GARAGE - NIGHT", "CUT TO the next scene",
    "Scene 2 opens with", "FADE IN on the workshop", "VOICEOVER: welcome back",
])
def test_script_markers_are_rejected(marker):
    with pytest.raises(OpportunityValidationError, match="script/production marker"):
        check(opportunity(angle=f"{marker} then the presenter explains the workflow."))


def test_multiple_timestamps_are_rejected_as_a_shot_list():
    with pytest.raises(OpportunityValidationError, match="shot list"):
        check(opportunity(angle="The video opens at 0:05 and returns to the same beat at 0:45."))


def test_a_single_timestamp_like_mention_does_not_trigger_the_shot_list_check():
    # One match alone is not enough to call it a shot list. "1:02" uses digits
    # (1, 2) that are already present elsewhere in the pack (insight titles),
    # so this also confirms the numeric-claim check does not spuriously fire.
    check(opportunity(angle="Similar to the moment at 1:02 in a well-known video, not a shot list."))


def test_hashtag_heavy_text_is_rejected_as_a_finished_caption():
    with pytest.raises(OpportunityValidationError, match="hashtags"):
        check(opportunity(cta_direction="Follow along #tradeflow #soletrader #quotes for more."))


def test_a_url_in_cta_direction_is_rejected():
    with pytest.raises(OpportunityValidationError, match="URL"):
        check(opportunity(cta_direction="Visit https://tradeflow.example.com to sign up now."))


# --- numeric claims ----------------------------------------------------------------------------


def test_a_number_not_present_anywhere_in_the_pack_is_rejected():
    with pytest.raises(OpportunityValidationError, match="numeric claim"):
        check(opportunity(opportunity="A guide covering the 37 most common repeat-job mistakes."))


def test_confidence_like_fractions_are_allowed_without_matching_exactly():
    # 0.6 (an insight's model_confidence) is present in the pack; the check
    # tolerates writing it as a rounded percentage.
    check(opportunity(limitations="Roughly 60% confidence based on a single insight."))


# --- zero-opportunity runs are always valid -----------------------------------------------------


def test_a_zero_opportunity_output_validates_trivially():
    out = OpportunityCreationOutput.from_dict(output(reason="Nothing connects to the product."))
    validated = validate_opportunities(out, pack(), PRODUCT)
    assert validated.opportunities == () and validated.no_opportunities_reason.startswith("Nothing")


# --- one bad opportunity among good ones rejects the whole run (checked at the service layer,
#     see test_opportunity_service.py); here we confirm the index is reported -------------------


def test_the_failing_opportunitys_index_is_reported():
    out = OpportunityCreationOutput.from_dict(output(opportunity(), opportunity(title="x" * 200)))
    with pytest.raises(OpportunityValidationError, match="opportunity 1"):
        validate_opportunities(out, pack(), PRODUCT)
