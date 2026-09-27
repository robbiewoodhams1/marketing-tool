"""Opportunity Creation output schema: what the opportunity model must return.

Pure data + strict shape validation: no I/O, no LLM calls, no database. This
only checks that the output is well formed. Whether it is *supported* - every
cited insight and product capability really exists, the text is not finished
content, and no product claim exceeds the supplied product context - is
`research.opportunity_validation`.

Research (`research.synthesis`) answers "what recurring patterns does the
dataset support?". Opportunity Creation answers "given those patterns AND what
the product actually does, what is worth producing?". An opportunity is a
creative/business problem to solve, scoped to one product, never finished
content and never a claim the product cannot back up.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

OPPORTUNITY_ANALYSIS_TYPE = "opportunity_creation"
# Version of the output shape below. Bump on an incompatible change.
OPPORTUNITY_SCHEMA_VERSION = "opportunity-v1"

# A readability/cost bound on one run's output, not an evidential threshold.
MAX_OPPORTUNITIES = 10
MAX_SUGGESTED_FORMATS = 5

# Opaque references assigned by research.opportunity_pack: N# for a supplied
# research insight, P# for a supplied product capability. Never a database id.
_INSIGHT_REF_RE = re.compile(r"^N[1-9][0-9]*$")
_CAPABILITY_REF_RE = re.compile(r"^P[1-9][0-9]*$")


class OpportunityError(Exception):
    """Base class for opportunity-creation failures."""


class OpportunitySchemaError(OpportunityError, ValueError):
    """The model output does not match the schema."""


class OpportunityValidationError(OpportunityError, ValueError):
    """The output is well formed but not supported by the supplied research/product context."""

    def __init__(self, message: str, opportunity_index: int | None = None):
        super().__init__(
            message if opportunity_index is None else f"opportunity {opportunity_index}: {message}"
        )
        self.opportunity_index = opportunity_index
        self.raw: Any = None  # the offending output, for development diagnostics


class MarketingObjective(str, Enum):
    """Deliberately small; extend only when an implementation needs it."""

    AWARENESS = "awareness"
    ACQUISITION = "acquisition"
    ACTIVATION = "activation"
    RETENTION = "retention"
    TRUST = "trust"


class SuggestedFormat(str, Enum):
    """A production-facing hint, not a spec. Deliberately small."""

    SHORT_FORM_VIDEO = "short_form_video"
    LONG_FORM_VIDEO = "long_form_video"
    TUTORIAL = "tutorial"
    CASE_STUDY = "case_study"
    COMPARISON = "comparison"
    CHECKLIST_OR_TEMPLATE = "checklist_or_template"
    BLOG_POST = "blog_post"
    SOCIAL_POST = "social_post"
    EMAIL = "email"


@dataclass(frozen=True)
class ResearchEvidenceRef:
    """One cited research insight, and why it justifies this opportunity."""

    ref: str  # opaque "N#", resolved to a real insight id by the validator
    note: str  # why this insight justifies the opportunity


@dataclass(frozen=True)
class ProductFit:
    """Why this opportunity is relevant to THIS product, not just the topic."""

    capability_refs: tuple[str, ...]  # opaque "P#"s, resolved to real capability keys
    explanation: str


@dataclass(frozen=True)
class Opportunity:
    title: str
    audience: str
    problem: str  # the problem/desire being addressed (from research)
    opportunity: str  # the concrete thing that could be made
    angle: str  # the creative/business angle
    marketing_objective: MarketingObjective
    suggested_formats: tuple[SuggestedFormat, ...]
    cta_direction: str  # a direction for the call to action, not finished copy
    research_evidence: tuple[ResearchEvidenceRef, ...]  # why we believe the audience cares
    product_fit: ProductFit  # why this is relevant to the product
    confidence: float  # the model's own 0-1 confidence; NOT a calibrated probability
    limitations: str


@dataclass(frozen=True)
class OpportunityCreationOutput:
    opportunities: tuple[Opportunity, ...]
    no_opportunities_reason: str | None

    @classmethod
    def from_dict(cls, data: Any) -> OpportunityCreationOutput:
        """Strictly parse the model output. Unknown or missing keys are errors."""
        obj = _mapping(data, "output", {"opportunities", "no_opportunities_reason"})
        raw = obj["opportunities"]
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise OpportunitySchemaError("opportunities must be a list")
        if len(raw) > MAX_OPPORTUNITIES:
            raise OpportunitySchemaError(f"at most {MAX_OPPORTUNITIES} opportunities per run")
        opportunities = tuple(_opportunity(o, n) for n, o in enumerate(raw))

        reason = obj["no_opportunities_reason"]
        if reason is not None and not (isinstance(reason, str) and reason.strip()):
            raise OpportunitySchemaError("no_opportunities_reason must be a non-blank string or null")
        if not opportunities and reason is None:
            raise OpportunitySchemaError("no opportunities requires a no_opportunities_reason")
        if opportunities and reason is not None:
            raise OpportunitySchemaError("no_opportunities_reason must be null when opportunities are returned")
        return cls(opportunities=opportunities, no_opportunities_reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunities": [_opportunity_dict(o) for o in self.opportunities],
            "no_opportunities_reason": self.no_opportunities_reason,
        }


# --- parsing helpers ---------------------------------------------------------


def _mapping(value: Any, where: str, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OpportunitySchemaError(f"{where} must be an object")
    if unknown := set(value) - keys:
        raise OpportunitySchemaError(f"{where}: unexpected keys {sorted(unknown)}")
    if missing := keys - set(value):
        raise OpportunitySchemaError(f"{where}: missing keys {sorted(missing)}")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpportunitySchemaError(f"{where} must be a non-blank string")
    return value.strip()


def _list(value: Any, where: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise OpportunitySchemaError(f"{where} must be a list")
    return value


def _enum(enum: type[Enum], value: Any, where: str) -> Any:
    try:
        return enum(value)
    except ValueError:
        allowed = sorted(m.value for m in enum)
        raise OpportunitySchemaError(f"{where}: {value!r} is not one of {allowed}") from None


def _research_evidence(value: Any, where: str) -> ResearchEvidenceRef:
    obj = _mapping(value, where, {"ref", "note"})
    ref = obj["ref"]
    if not isinstance(ref, str) or not _INSIGHT_REF_RE.match(ref):
        raise OpportunitySchemaError(f"{where}: malformed insight reference {ref!r}")
    return ResearchEvidenceRef(ref=ref, note=_text(obj["note"], f"{where}.note"))


def _product_fit(value: Any, where: str) -> ProductFit:
    obj = _mapping(value, where, {"capability_refs", "explanation"})
    raw_refs = _list(obj["capability_refs"], f"{where}.capability_refs")
    if not raw_refs:
        raise OpportunitySchemaError(f"{where}: product_fit needs at least one capability reference")
    refs: list[str] = []
    for n, ref in enumerate(raw_refs):
        if not isinstance(ref, str) or not _CAPABILITY_REF_RE.match(ref):
            raise OpportunitySchemaError(f"{where}.capability_refs[{n}]: malformed capability reference {ref!r}")
        refs.append(ref)
    if len(set(refs)) != len(refs):
        raise OpportunitySchemaError(f"{where}.capability_refs: duplicate references {refs}")
    return ProductFit(capability_refs=tuple(refs), explanation=_text(obj["explanation"], f"{where}.explanation"))


def _opportunity(value: Any, index: int) -> Opportunity:
    where = f"opportunities[{index}]"
    obj = _mapping(
        value,
        where,
        {
            "title", "audience", "problem", "opportunity", "angle", "marketing_objective",
            "suggested_formats", "cta_direction", "research_evidence", "product_fit",
            "confidence", "limitations",
        },
    )
    research_evidence = tuple(
        _research_evidence(e, f"{where}.research_evidence[{n}]")
        for n, e in enumerate(_list(obj["research_evidence"], f"{where}.research_evidence"))
    )
    if not research_evidence:
        raise OpportunitySchemaError(f"{where}: an opportunity needs at least one research_evidence entry")
    if len({e.ref for e in research_evidence}) != len(research_evidence):
        raise OpportunitySchemaError(f"{where}.research_evidence: the same insight reference repeats")

    raw_formats = _list(obj["suggested_formats"], f"{where}.suggested_formats")
    if not raw_formats:
        raise OpportunitySchemaError(f"{where}: suggested_formats must not be empty")
    if len(raw_formats) > MAX_SUGGESTED_FORMATS:
        raise OpportunitySchemaError(f"{where}: at most {MAX_SUGGESTED_FORMATS} suggested_formats")
    formats = tuple(
        _enum(SuggestedFormat, f, f"{where}.suggested_formats[{n}]") for n, f in enumerate(raw_formats)
    )
    if len(set(formats)) != len(formats):
        raise OpportunitySchemaError(f"{where}.suggested_formats: duplicate formats are not allowed")

    confidence = obj["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise OpportunitySchemaError(f"{where}.confidence must be a number")
    if not 0.0 <= confidence <= 1.0:
        raise OpportunitySchemaError(f"{where}.confidence must be between 0 and 1")

    return Opportunity(
        title=_text(obj["title"], f"{where}.title"),
        audience=_text(obj["audience"], f"{where}.audience"),
        problem=_text(obj["problem"], f"{where}.problem"),
        opportunity=_text(obj["opportunity"], f"{where}.opportunity"),
        angle=_text(obj["angle"], f"{where}.angle"),
        marketing_objective=_enum(MarketingObjective, obj["marketing_objective"], f"{where}.marketing_objective"),
        suggested_formats=formats,
        cta_direction=_text(obj["cta_direction"], f"{where}.cta_direction"),
        research_evidence=research_evidence,
        product_fit=_product_fit(obj["product_fit"], f"{where}.product_fit"),
        confidence=float(confidence),
        limitations=_text(obj["limitations"], f"{where}.limitations"),
    )


def _research_evidence_dict(e: ResearchEvidenceRef) -> dict[str, str]:
    return {"ref": e.ref, "note": e.note}


def _product_fit_dict(p: ProductFit) -> dict[str, Any]:
    return {"capability_refs": list(p.capability_refs), "explanation": p.explanation}


def _opportunity_dict(o: Opportunity) -> dict[str, Any]:
    return {
        "title": o.title,
        "audience": o.audience,
        "problem": o.problem,
        "opportunity": o.opportunity,
        "angle": o.angle,
        "marketing_objective": o.marketing_objective.value,
        "suggested_formats": [f.value for f in o.suggested_formats],
        "cta_direction": o.cta_direction,
        "research_evidence": [_research_evidence_dict(e) for e in o.research_evidence],
        "product_fit": _product_fit_dict(o.product_fit),
        "confidence": o.confidence,
        "limitations": o.limitations,
    }
