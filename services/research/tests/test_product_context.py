import pytest
from opportunity_fixtures import PRODUCT, product

from research.product_context import ProductCapability, ProductContextError, ProductProfile
from research.products import UnknownProductError, get_product_profile, known_product_keys
from research.products.tradeflow import TRADEFLOW_PROFILE


def capability(**overrides):
    fields = dict(key="quotes", name="Quotes", description="Create quotes.", problem_solved="Producing a quote.")
    fields.update(overrides)
    return ProductCapability(**fields)


# --- ProductCapability --------------------------------------------------------------------


def test_capability_key_must_be_lowercase_snake_case():
    capability(key="quotes")  # ok
    for bad_key in ("Quotes", "quotes-1", "1quotes", "quotes here", ""):
        with pytest.raises(ProductContextError):
            capability(key=bad_key)


@pytest.mark.parametrize("field", ["name", "description", "problem_solved"])
def test_capability_text_fields_must_not_be_blank(field):
    with pytest.raises(ProductContextError):
        capability(**{field: "   "})


# --- ProductProfile ------------------------------------------------------------------------


def test_a_valid_profile_builds():
    p = product()
    assert p.key == "testproduct" and len(p.capabilities) == 2


def test_product_key_must_be_lowercase_snake_case():
    with pytest.raises(ProductContextError):
        product(key="Test Product")


def test_version_name_description_must_not_be_blank():
    for field in ("version", "name", "description"):
        with pytest.raises(ProductContextError):
            product(**{field: "  "})


def test_at_least_one_capability_is_required():
    with pytest.raises(ProductContextError, match="at least one capability"):
        product(capabilities=())


def test_duplicate_capability_keys_are_rejected():
    with pytest.raises(ProductContextError, match="duplicate capability keys"):
        product(capabilities=(capability(key="quotes"), capability(key="quotes", name="Quotes again")))


def test_blank_audience_entries_are_rejected():
    with pytest.raises(ProductContextError):
        product(audience=("UK sole traders", "  "))


def test_capability_lookup_by_key():
    p = product()
    assert p.capability("quotes").name == "Quotes"
    assert p.capability("nonexistent") is None


def test_absent_fields_stay_none_or_empty_not_invented():
    p = product()
    assert p.pricing is None
    assert p.limitations == () and p.marketing_objectives == ()


def test_to_manifest_is_a_complete_deterministic_snapshot():
    p = product()
    manifest = p.to_manifest()
    assert manifest["key"] == "testproduct" and manifest["version"] == "testproduct-v1"
    assert len(manifest["capabilities"]) == 2
    assert manifest["capabilities"][0] == {
        "key": "quotes", "name": "Quotes", "description": "Create and send quotes.",
        "problem_solved": "Producing a quote for a customer.",
    }
    assert manifest["pricing"] is None
    # deterministic: calling it twice gives the same result
    assert p.to_manifest() == manifest


# --- product registry ----------------------------------------------------------------------


def test_tradeflow_is_registered_and_resolvable():
    assert get_product_profile("tradeflow") is TRADEFLOW_PROFILE
    assert "tradeflow" in known_product_keys()


def test_unknown_product_key_is_rejected():
    with pytest.raises(UnknownProductError, match="tradeflow"):
        get_product_profile("houseflow")


# --- the real TradeFlow profile: only verified facts, nothing invented --------------------


def test_tradeflow_profile_contains_only_verified_capabilities():
    keys = {c.key for c in TRADEFLOW_PROFILE.capabilities}
    assert keys == {
        "quotes", "jobs", "invoices", "reusable_templates", "saved_customers",
        "earnings_overview", "trade_specific_customisation", "email_delivery",
    }


def test_tradeflow_pricing_is_unknown_not_invented():
    assert TRADEFLOW_PROFILE.pricing is None


def test_tradeflow_out_of_scope_lists_what_it_explicitly_does_not_do():
    out_of_scope = " ".join(TRADEFLOW_PROFILE.out_of_scope).lower()
    for phrase in ("bookkeeping", "tax return", "accounting", "mileage", "payroll", "crm"):
        assert phrase in out_of_scope
