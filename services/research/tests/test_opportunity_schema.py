import pytest
from opportunity_fixtures import GOOD_OUTPUT, ZERO_OUTPUT, opportunity, output, product_fit, research_evidence

from research.opportunity import (
    MAX_OPPORTUNITIES,
    MAX_SUGGESTED_FORMATS,
    MarketingObjective,
    OpportunityCreationOutput,
    OpportunitySchemaError,
    SuggestedFormat,
)
from research.opportunity_prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
    opportunity_output_json_schema,
)


def parse(data):
    return OpportunityCreationOutput.from_dict(data)


def bad(**changes):
    """GOOD_OUTPUT with the first opportunity's fields replaced."""
    return output(opportunity(**changes))


# --- valid --------------------------------------------------------------------------------


def test_valid_output_parses_and_roundtrips():
    out = parse(GOOD_OUTPUT)
    [o] = out.opportunities
    assert o.marketing_objective is MarketingObjective.ACQUISITION
    assert o.suggested_formats == (SuggestedFormat.TUTORIAL,)
    assert o.research_evidence[0].ref == "N1"
    assert o.product_fit.capability_refs == ("P1",)
    assert out.no_opportunities_reason is None
    assert out.to_dict() == GOOD_OUTPUT  # nothing added, nothing lost


def test_zero_opportunity_output_is_valid_with_a_reason():
    out = parse(ZERO_OUTPUT)
    assert out.opportunities == () and "connects" in out.no_opportunities_reason


def test_taxonomies_are_small_and_as_specified():
    assert [m.value for m in MarketingObjective] == [
        "awareness", "acquisition", "activation", "retention", "trust"]
    assert [f.value for f in SuggestedFormat] == [
        "short_form_video", "long_form_video", "tutorial", "case_study", "comparison",
        "checklist_or_template", "blog_post", "social_post", "email"]


# --- malformed top level --------------------------------------------------------------------


@pytest.mark.parametrize("data", [None, [], "text", 3, {"opportunities": []}, {"no_opportunities_reason": "x"}])
def test_missing_or_non_object_output_is_rejected(data):
    with pytest.raises(OpportunitySchemaError):
        parse(data)


def test_unknown_top_level_and_opportunity_keys_are_rejected():
    with pytest.raises(OpportunitySchemaError, match="unexpected"):
        parse({**GOOD_OUTPUT, "extra": 1})
    with pytest.raises(OpportunitySchemaError, match="unexpected"):
        parse(bad(extra_field="oops"))


def test_too_many_opportunities_is_rejected():
    with pytest.raises(OpportunitySchemaError, match=f"at most {MAX_OPPORTUNITIES}"):
        parse(output(*[opportunity() for _ in range(MAX_OPPORTUNITIES + 1)]))


def test_opportunities_must_be_a_list():
    with pytest.raises(OpportunitySchemaError, match="must be a list"):
        parse({"opportunities": "not a list", "no_opportunities_reason": None})


@pytest.mark.parametrize("reason,ok", [(None, False), ("", False), ("   ", False), ("a real reason", True)])
def test_no_opportunities_reason_must_be_a_non_blank_string_when_empty(reason, ok):
    data = output(reason=reason)
    if ok:
        assert parse(data).no_opportunities_reason == reason
    else:
        with pytest.raises(OpportunitySchemaError):
            parse(data)


def test_a_reason_alongside_opportunities_is_rejected():
    with pytest.raises(OpportunitySchemaError, match="must be null"):
        parse(output(opportunity(), reason="also a reason"))


def test_no_opportunities_without_a_reason_is_rejected():
    with pytest.raises(OpportunitySchemaError, match="requires a no_opportunities_reason"):
        parse({"opportunities": [], "no_opportunities_reason": None})


# --- malformed fields ------------------------------------------------------------------------


@pytest.mark.parametrize("field", [
    "title", "audience", "problem", "opportunity", "angle", "marketing_objective",
    "suggested_formats", "cta_direction", "research_evidence", "product_fit",
    "confidence", "limitations",
])
def test_each_required_field_is_required(field):
    with pytest.raises(OpportunitySchemaError, match="missing keys"):
        parse(output({k: v for k, v in opportunity().items() if k != field}))


@pytest.mark.parametrize("field", ["title", "audience", "problem", "opportunity", "angle", "cta_direction", "limitations"])
def test_text_fields_must_not_be_blank(field):
    with pytest.raises(OpportunitySchemaError):
        parse(bad(**{field: "   "}))


def test_marketing_objective_must_be_a_known_value():
    with pytest.raises(OpportunitySchemaError, match="is not one of"):
        parse(bad(marketing_objective="world_domination"))


def test_suggested_formats_must_not_be_empty():
    with pytest.raises(OpportunitySchemaError, match="must not be empty"):
        parse(bad(suggested_formats=[]))


def test_suggested_formats_rejects_unknown_values():
    with pytest.raises(OpportunitySchemaError, match="is not one of"):
        parse(bad(suggested_formats=["billboard"]))


def test_suggested_formats_rejects_duplicates():
    with pytest.raises(OpportunitySchemaError, match="duplicate"):
        parse(bad(suggested_formats=["tutorial", "tutorial"]))


def test_suggested_formats_enforces_a_maximum():
    with pytest.raises(OpportunitySchemaError, match=f"at most {MAX_SUGGESTED_FORMATS}"):
        parse(bad(suggested_formats=[f.value for f in SuggestedFormat][:MAX_SUGGESTED_FORMATS + 1]))


@pytest.mark.parametrize("value", [None, "0.5", True, False, -0.01, 1.01])
def test_confidence_must_be_a_number_between_0_and_1(value):
    with pytest.raises(OpportunitySchemaError, match="confidence"):
        parse(bad(confidence=value))


def test_confidence_boundaries_are_inclusive():
    assert parse(bad(confidence=0.0)).opportunities[0].confidence == 0.0
    assert parse(bad(confidence=1.0)).opportunities[0].confidence == 1.0


# --- research_evidence ------------------------------------------------------------------------


def test_research_evidence_must_be_non_empty():
    with pytest.raises(OpportunitySchemaError, match="at least one research_evidence"):
        parse(bad(research_evidence=[]))


def test_research_evidence_ref_must_match_the_n_pattern():
    with pytest.raises(OpportunitySchemaError, match="malformed insight reference"):
        parse(bad(research_evidence=[research_evidence(ref="C1")]))
    with pytest.raises(OpportunitySchemaError, match="malformed insight reference"):
        parse(bad(research_evidence=[research_evidence(ref="N0")]))  # no leading zero / zero index


def test_research_evidence_rejects_repeated_refs():
    with pytest.raises(OpportunitySchemaError, match="repeats"):
        parse(bad(research_evidence=[research_evidence(ref="N1"), research_evidence(ref="N1")]))


def test_research_evidence_note_must_not_be_blank():
    with pytest.raises(OpportunitySchemaError):
        parse(bad(research_evidence=[research_evidence(note="  ")]))


# --- product_fit -------------------------------------------------------------------------------


def test_product_fit_requires_at_least_one_capability_ref():
    with pytest.raises(OpportunitySchemaError, match="at least one capability"):
        parse(bad(product_fit=product_fit(capability_refs=())))


def test_product_fit_capability_ref_must_match_the_p_pattern():
    with pytest.raises(OpportunitySchemaError, match="malformed capability reference"):
        parse(bad(product_fit=product_fit(capability_refs=("N1",))))


def test_product_fit_rejects_duplicate_capability_refs():
    with pytest.raises(OpportunitySchemaError, match="duplicate"):
        parse(bad(product_fit=product_fit(capability_refs=("P1", "P1"))))


def test_product_fit_explanation_must_not_be_blank():
    with pytest.raises(OpportunitySchemaError):
        parse(bad(product_fit=product_fit(explanation="   ")))


# --- prompt / json schema -----------------------------------------------------------------------


def test_prompt_version_is_set():
    assert PROMPT_VERSION == "opportunity-v1"


def test_system_prompt_states_the_core_rules():
    for phrase in ("product_fit", "research_evidence", "Don't force it", "finished content"):
        assert phrase in SYSTEM_PROMPT


def test_json_schema_is_strict_and_matches_the_dataclass_fields():
    schema = opportunity_output_json_schema()
    assert schema["additionalProperties"] is False
    opp_schema = schema["properties"]["opportunities"]["items"]
    assert opp_schema["additionalProperties"] is False
    assert set(opp_schema["required"]) == {
        "title", "audience", "problem", "opportunity", "angle", "marketing_objective",
        "suggested_formats", "cta_direction", "research_evidence", "product_fit",
        "confidence", "limitations",
    }
    assert opp_schema["properties"]["marketing_objective"]["enum"] == [m.value for m in MarketingObjective]


def test_build_user_prompt_wraps_the_pack_json_losslessly():
    from opportunity_fixtures import JOB, PRODUCT
    from research.opportunity_pack import build_opportunity_pack

    pack = build_opportunity_pack(
        job=JOB, synthesis={"id": "syn-1", "research_job_id": "job-1"},
        insight_rows=[{
            "id": "insight-1", "title": "t", "statement": "s", "pattern_type": "recurring_theme",
            "explanation": "e", "model_confidence": 0.5, "confidence_rationale": "r",
            "limitations": "l", "support": {},
        }],
        product=PRODUCT,
    )
    prompt = build_user_prompt(pack)
    assert prompt.count("```") == 2
    import json as json_module

    body = prompt.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    assert json_module.loads(body) == pack.prompt
