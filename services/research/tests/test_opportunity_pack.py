import pytest
from opportunity_fixtures import JOB, PRODUCT, insight_row

from research.opportunity_pack import (
    MIN_INSIGHTS_FOR_OPPORTUNITIES,
    InsufficientResearchError,
    build_opportunity_pack,
)

SYNTHESIS = {"id": "syn-1", "research_job_id": "job-1"}


def build(rows, product=PRODUCT):
    return build_opportunity_pack(job=JOB, synthesis=SYNTHESIS, insight_rows=rows, product=product)


def test_too_few_insights_raises_before_any_llm_call():
    with pytest.raises(InsufficientResearchError, match=f"at least {MIN_INSIGHTS_FOR_OPPORTUNITIES}"):
        build([])


def test_one_insight_is_enough():
    pack = build([insight_row(1)])
    assert pack.insight_ids == ("insight-1",)


def test_insight_refs_are_opaque_and_map_back_to_real_ids():
    pack = build([insight_row(1), insight_row(2)])
    assert set(pack.insight_refs) == {"N1", "N2"}
    assert {pack.insight_refs[r] for r in pack.insight_refs} == {"insight-1", "insight-2"}


def test_ref_assignment_is_deterministic_regardless_of_input_order():
    forward = build([insight_row(1), insight_row(2)])
    backward = build([insight_row(2), insight_row(1)])
    assert forward.insight_refs == backward.insight_refs


def test_capability_refs_cover_every_supplied_capability():
    pack = build([insight_row(1)])
    assert set(pack.capability_refs) == {"P1", "P2"}
    assert set(pack.capability_refs.values()) == {"quotes", "reusable_templates"}


def test_prompt_never_leaks_database_ids():
    pack = build([insight_row(1)])
    assert "insight-1" not in pack.prompt_json()
    assert '"ref": "N1"' in pack.prompt_json()


def test_prompt_carries_the_research_job_and_product_context():
    pack = build([insight_row(1)])
    assert pack.prompt["research_job"] == JOB
    assert pack.prompt["product"]["key"] == "testproduct"
    assert pack.prompt["product"]["pricing"] is None


def test_manifest_records_provenance_and_product_identity():
    pack = build([insight_row(1), insight_row(2)])
    assert pack.manifest["synthesis_id"] == "syn-1"
    assert pack.manifest["research_job_id"] == "job-1"
    assert pack.manifest["product_key"] == "testproduct"
    assert pack.manifest["product_version"] == "testproduct-v1"
    assert set(pack.manifest["selected_insight_ids"]) == {"insight-1", "insight-2"}
    assert pack.manifest["product_profile"] == PRODUCT.to_manifest()


def test_a_different_product_profile_produces_a_different_pack():
    from opportunity_fixtures import product

    other = product(key="other", version="other-v1")
    pack_a = build([insight_row(1)], product=PRODUCT)
    pack_b = build([insight_row(1)], product=other)
    assert pack_a.manifest["product_key"] != pack_b.manifest["product_key"]
