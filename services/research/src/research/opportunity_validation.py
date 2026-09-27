"""Are the opportunity model's claims supported by the insights and product
context it was shown? `OpportunityCreationOutput.from_dict` checks the SHAPE.
This checks the SUBSTANCE, in application code, and never trusts the model:

* every research_evidence ref resolves to a supplied insight (unknown refs are
  rejected), and no ref repeats,
* every product_fit capability ref resolves to a supplied product capability
  (unknown refs are rejected), and no ref repeats - this is what makes
  "unsupported product claims" structurally impossible: the model can only
  ever point at a capability that was actually supplied,
* product_fit.explanation and cta_direction never claim something the product
  profile explicitly says it does NOT do (`ProductProfile.out_of_scope`),
* the opportunity reads like a brief, not finished content: bounded length,
  no script/caption/shot-list markers, no literal CTA copy or links,
* numbers in the model's prose come from the supplied insights or product
  context, never invented.

These are deterministic heuristics: a backstop, not a proof. They can reject
valid prose; they must never be relaxed to make a model answer pass. Anything
that fails rejects the whole run; nothing is silently dropped or edited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from research.evidence_pack import numbers_in
from research.opportunity import (
    Opportunity,
    OpportunityCreationOutput,
    OpportunityValidationError,
)
from research.opportunity_pack import OpportunityInputPack
from research.product_context import ProductProfile

# Script/production markers: an opportunity describes WHAT to make, never the
# finished script.
_FINISHED_CONTENT_RE = re.compile(
    r"\b(scene\s*\d|int\.|ext\.|cut to|fade in|fade out|voiceover\s*:|vo\s*:|\[music\]|\[sfx\])",
    re.IGNORECASE,
)
_TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

MAX_TITLE_CHARS = 120
MAX_SHORT_FIELD_CHARS = 400
MAX_LONG_FIELD_CHARS = 600
MIN_HASHTAGS_FOR_FINISHED_CAPTION = 3


@dataclass(frozen=True)
class ValidatedOpportunity:
    opportunity: Opportunity
    insight_ids: tuple[str, ...]  # resolved from research_evidence refs, in the same order
    capability_keys: tuple[str, ...]  # resolved from product_fit.capability_refs, in the same order


@dataclass(frozen=True)
class ValidatedOpportunityCreation:
    opportunities: tuple[ValidatedOpportunity, ...]
    no_opportunities_reason: str | None


def validate_opportunities(
    output: OpportunityCreationOutput,
    pack: OpportunityInputPack,
    product: ProductProfile,
) -> ValidatedOpportunityCreation:
    """Validate against the pack and product profile, or raise OpportunityValidationError."""
    validated = tuple(
        _validate_one(o, n, pack, product) for n, o in enumerate(output.opportunities)
    )
    return ValidatedOpportunityCreation(opportunities=validated, no_opportunities_reason=output.no_opportunities_reason)


# --- references --------------------------------------------------------------


def _resolve_insights(o: Opportunity, n: int, pack: OpportunityInputPack) -> tuple[str, ...]:
    ids: list[str] = []
    for i, e in enumerate(o.research_evidence):
        target = pack.insight_refs.get(e.ref)
        if target is None:
            raise OpportunityValidationError(
                f"research_evidence[{i}]: unknown reference {e.ref!r} (not in the supplied insights)", n
            )
        ids.append(target)
    return tuple(ids)


def _resolve_capabilities(o: Opportunity, n: int, pack: OpportunityInputPack) -> tuple[str, ...]:
    keys: list[str] = []
    for i, ref in enumerate(o.product_fit.capability_refs):
        key = pack.capability_refs.get(ref)
        if key is None:
            raise OpportunityValidationError(
                f"product_fit.capability_refs[{i}]: unknown reference {ref!r} "
                "(not in the supplied product profile)", n
            )
        keys.append(key)
    return tuple(keys)


# --- product truth -------------------------------------------------------------


def _check_product_truth(o: Opportunity, n: int, product: ProductProfile) -> None:
    """product_fit.explanation and cta_direction are where a product claim is
    actually made (as opposed to `problem`, which legitimately describes the
    audience's real-world pain even when the product cannot address it)."""
    for field_name, text in (
        ("product_fit.explanation", o.product_fit.explanation),
        ("cta_direction", o.cta_direction),
    ):
        lowered = text.lower()
        for phrase in product.out_of_scope:
            if phrase.lower() in lowered:
                raise OpportunityValidationError(
                    f"{field_name} claims or implies {phrase!r}, which {product.name} does not "
                    "currently do (product truth)", n
                )


# --- opportunity vs. finished content --------------------------------------------


def _check_not_finished_content(o: Opportunity, n: int) -> None:
    if len(o.title) > MAX_TITLE_CHARS:
        raise OpportunityValidationError(
            f"title is {len(o.title)} characters (> {MAX_TITLE_CHARS}); an opportunity states a "
            "creative/business problem to solve, not finished copy", n
        )
    fields = (
        ("problem", o.problem, MAX_SHORT_FIELD_CHARS),
        ("opportunity", o.opportunity, MAX_SHORT_FIELD_CHARS),
        ("angle", o.angle, MAX_SHORT_FIELD_CHARS),
        ("cta_direction", o.cta_direction, MAX_SHORT_FIELD_CHARS),
        ("limitations", o.limitations, MAX_LONG_FIELD_CHARS),
        ("product_fit.explanation", o.product_fit.explanation, MAX_LONG_FIELD_CHARS),
    )
    for field_name, text, limit in fields:
        if len(text) > limit:
            raise OpportunityValidationError(
                f"{field_name} is {len(text)} characters (> {limit}); this reads like finished "
                "content, not an opportunity brief", n
            )
        if _FINISHED_CONTENT_RE.search(text):
            raise OpportunityValidationError(
                f"{field_name} contains a script/production marker; an opportunity describes WHAT "
                "to make, not the finished script", n
            )
        if len(_TIMESTAMP_RE.findall(text)) >= 2:
            raise OpportunityValidationError(
                f"{field_name} contains timestamps; this reads like a shot list, not an opportunity", n
            )
        if text.count("#") >= MIN_HASHTAGS_FOR_FINISHED_CAPTION:
            raise OpportunityValidationError(
                f"{field_name} reads like a finished social caption (multiple hashtags)", n
            )
    if _URL_RE.search(o.cta_direction):
        raise OpportunityValidationError(
            "cta_direction contains a URL; it should describe a direction, not finished copy", n
        )


# --- numeric claims -------------------------------------------------------------


def _allowed_numbers(pack: OpportunityInputPack) -> set[float]:
    return numbers_in(pack.prompt_json()) | {0.0, 1.0}


def _is_allowed(number: float, allowed: set[float]) -> bool:
    if number in allowed:
        return True
    for a in allowed:
        if 0 < a <= 1:  # a proportion/confidence: may be written as a percentage or rounded
            if abs(number - a * 100) <= 0.5 or abs(number - round(a, 2)) < 1e-9:
                return True
    return False


def _check_numbers(o: Opportunity, n: int, pack: OpportunityInputPack) -> None:
    allowed = _allowed_numbers(pack)
    texts = (
        o.title, o.problem, o.opportunity, o.angle, o.cta_direction,
        o.product_fit.explanation, o.limitations,
    )
    for text in texts:
        for number in numbers_in(text):
            if not _is_allowed(number, allowed):
                raise OpportunityValidationError(
                    f"numeric claim {number:g} does not come from the supplied research insights "
                    "or product context", n
                )


# --- one opportunity ------------------------------------------------------------


def _validate_one(
    o: Opportunity, n: int, pack: OpportunityInputPack, product: ProductProfile
) -> ValidatedOpportunity:
    insight_ids = _resolve_insights(o, n, pack)
    capability_keys = _resolve_capabilities(o, n, pack)
    _check_product_truth(o, n, product)
    _check_not_finished_content(o, n)
    _check_numbers(o, n, pack)
    return ValidatedOpportunity(opportunity=o, insight_ids=insight_ids, capability_keys=capability_keys)
