from opportunity_fixtures import PRODUCT, product
from production_fixtures import opportunity_row

from research.production_pack import build_production_pack

INSIGHTS = [
    {"id": "insight-1", "title": "T1", "statement": "S1", "limitations": "L1"},
    {"id": "insight-2", "title": "T2", "statement": "S2", "limitations": "L2"},
]


def build(**kw):
    kw.setdefault("opportunity", opportunity_row())
    kw.setdefault("research_context", INSIGHTS)
    kw.setdefault("product", PRODUCT)
    return build_production_pack(**kw)


def test_capability_refs_cover_every_supplied_capability():
    pack = build()
    assert set(pack.capability_refs) == {"P1", "P2"}
    assert set(pack.capability_refs.values()) == {"quotes", "reusable_templates"}


def test_prompt_never_leaks_database_ids_for_capabilities():
    pack = build()
    assert '"ref": "P1"' in pack.prompt_json()
    assert '"key": "quotes"' not in pack.prompt_json()  # only the ref, not the raw key


def test_prompt_carries_the_opportunity_as_the_creative_brief():
    pack = build()
    assert pack.prompt["opportunity"]["title"] == "Practical templates in action"
    assert pack.prompt["opportunity"]["angle"]


def test_prompt_carries_research_context_as_background_only():
    pack = build()
    assert pack.prompt["research_context"] == [
        {"title": "T1", "statement": "S1", "limitations": "L1"},
        {"title": "T2", "statement": "S2", "limitations": "L2"},
    ]
    assert "insight-1" not in pack.prompt_json()  # opaque: no database ids leaked


def test_empty_research_context_is_allowed():
    pack = build(research_context=[])
    assert pack.prompt["research_context"] == []


def test_manifest_records_provenance_and_product_identity():
    pack = build()
    assert pack.manifest["opportunity_id"] == "opp-1"
    assert pack.manifest["product_key"] == "testproduct"
    assert pack.manifest["product_version"] == "testproduct-v1"
    assert set(pack.manifest["research_context_insight_ids"]) == {"insight-1", "insight-2"}
    assert pack.manifest["product_profile"] == PRODUCT.to_manifest()


def test_a_different_product_profile_produces_a_different_pack():
    other = product(key="other", version="other-v1")
    a = build(product=PRODUCT)
    b = build(product=other)
    assert a.manifest["product_key"] != b.manifest["product_key"]


def test_prompt_carries_the_product_context():
    pack = build()
    assert pack.prompt["product"]["key"] == "testproduct"
    assert pack.prompt["product"]["pricing"] is None
