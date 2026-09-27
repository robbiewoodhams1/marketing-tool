"""Synthesis output schema: what the synthesis model must return.

Pure data + strict shape validation: no I/O, no LLM calls, no database. This
only checks that the output is well formed. Whether it is *supported by the
evidence* (references exist, quotes are verbatim, recurring patterns have
enough content...) is `research.synthesis_validation`.

Classification answers "what is this one video?". Synthesis answers "what
recurring patterns does the whole research dataset support?". An insight is a
falsifiable finding scoped to the analysed dataset, never marketing advice.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

SYNTHESIS_ANALYSIS_TYPE = "insight_synthesis"
# Version of the output shape + taxonomies below. Bump on an incompatible change.
SYNTHESIS_SCHEMA_VERSION = "synthesis-v1"

# A readability/cost bound on one run's output, not an evidential threshold.
MAX_INSIGHTS = 10

STATS_REF = "STATS"  # the dataset-level computed statistics block
_REF_RE = re.compile(r"^(?:[CIM][1-9][0-9]*|STATS)$")


class SynthesisError(Exception):
    """Base class for synthesis failures."""


class SynthesisSchemaError(SynthesisError, ValueError):
    """The model output does not match the schema."""


class SynthesisValidationError(SynthesisError, ValueError):
    """The output is well formed but not supported by the supplied evidence."""

    def __init__(self, message: str, insight_index: int | None = None):
        super().__init__(
            message if insight_index is None else f"insight {insight_index}: {message}"
        )
        self.insight_index = insight_index
        self.raw: Any = None  # the offending output, for development diagnostics


class PatternType(str, Enum):
    """Deliberately small; extend only when an implementation needs it."""

    RECURRING_THEME = "recurring_theme"
    HOOK_PATTERN = "hook_pattern"
    FORMAT_PATTERN = "format_pattern"
    PERFORMANCE_SIGNAL = "performance_signal"
    CONTENT_COMMENT_PATTERN = "content_comment_pattern"


class EvidenceField(str, Enum):
    """Which supplied text a quote comes from."""

    TITLE = "title"
    DESCRIPTION = "description"
    TRANSCRIPT = "transcript"
    METRICS = "metrics"  # the raw/computed metrics text of a content item
    CLASSIFICATION = "classification"  # the classification text of an interpretation
    COMMENT_TEXT = "comment_text"
    STATISTICS = "statistics"  # the dataset statistics text (ref STATS)


@dataclass(frozen=True)
class EvidenceRef:
    ref: str
    field: EvidenceField
    quote: str


@dataclass(frozen=True)
class Observation:
    """An observed fact. Every observation must cite evidence."""

    text: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class Insight:
    title: str
    pattern_type: PatternType
    statement: str  # the inferred, falsifiable, dataset-scoped pattern
    observations: tuple[Observation, ...]
    explanation: str  # why the observations support the statement
    confidence: float  # the model's own 0-1 confidence; NOT a calibrated probability
    confidence_rationale: str
    counter_evidence: tuple[EvidenceRef, ...]
    limitations: str


@dataclass(frozen=True)
class SynthesisOutput:
    insights: tuple[Insight, ...]
    no_insights_reason: str | None

    @classmethod
    def from_dict(cls, data: Any) -> SynthesisOutput:
        """Strictly parse the model output. Unknown or missing keys are errors."""
        obj = _mapping(data, "output", {"insights", "no_insights_reason"})
        raw_insights = obj["insights"]
        if not isinstance(raw_insights, Sequence) or isinstance(raw_insights, (str, bytes)):
            raise SynthesisSchemaError("insights must be a list")
        if len(raw_insights) > MAX_INSIGHTS:
            raise SynthesisSchemaError(f"at most {MAX_INSIGHTS} insights per run")
        insights = tuple(_insight(i, n) for n, i in enumerate(raw_insights))

        reason = obj["no_insights_reason"]
        if reason is not None and not (isinstance(reason, str) and reason.strip()):
            raise SynthesisSchemaError("no_insights_reason must be a non-blank string or null")
        if not insights and reason is None:
            raise SynthesisSchemaError("no insights requires a no_insights_reason")
        if insights and reason is not None:
            raise SynthesisSchemaError("no_insights_reason must be null when insights are returned")
        return cls(insights=insights, no_insights_reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "insights": [_insight_dict(i) for i in self.insights],
            "no_insights_reason": self.no_insights_reason,
        }


# --- parsing helpers ---------------------------------------------------------


def _mapping(value: Any, where: str, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SynthesisSchemaError(f"{where} must be an object")
    if unknown := set(value) - keys:
        raise SynthesisSchemaError(f"{where}: unexpected keys {sorted(unknown)}")
    if missing := keys - set(value):
        raise SynthesisSchemaError(f"{where}: missing keys {sorted(missing)}")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SynthesisSchemaError(f"{where} must be a non-blank string")
    return value.strip()


def _list(value: Any, where: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SynthesisSchemaError(f"{where} must be a list")
    return value


def _enum(enum: type[Enum], value: Any, where: str) -> Any:
    try:
        return enum(value)
    except ValueError:
        allowed = sorted(m.value for m in enum)
        raise SynthesisSchemaError(f"{where}: {value!r} is not one of {allowed}") from None


def _evidence(value: Any, where: str) -> EvidenceRef:
    obj = _mapping(value, where, {"ref", "field", "quote"})
    ref = obj["ref"]
    if not isinstance(ref, str) or not _REF_RE.match(ref):
        raise SynthesisSchemaError(f"{where}: malformed ref {ref!r}")
    return EvidenceRef(
        ref=ref,
        field=_enum(EvidenceField, obj["field"], f"{where}.field"),
        quote=_text(obj["quote"], f"{where}.quote"),
    )


def _observation(value: Any, where: str) -> Observation:
    obj = _mapping(value, where, {"text", "evidence"})
    evidence = tuple(
        _evidence(e, f"{where}.evidence[{n}]")
        for n, e in enumerate(_list(obj["evidence"], f"{where}.evidence"))
    )
    if not evidence:
        raise SynthesisSchemaError(f"{where}: an observation needs evidence")
    return Observation(text=_text(obj["text"], f"{where}.text"), evidence=evidence)


def _insight(value: Any, index: int) -> Insight:
    where = f"insights[{index}]"
    obj = _mapping(
        value,
        where,
        {
            "title", "pattern_type", "statement", "observations", "explanation",
            "confidence", "confidence_rationale", "counter_evidence", "limitations",
        },
    )
    observations = tuple(
        _observation(o, f"{where}.observations[{n}]")
        for n, o in enumerate(_list(obj["observations"], f"{where}.observations"))
    )
    if not observations:
        raise SynthesisSchemaError(f"{where}: an insight needs at least one observation")
    confidence = obj["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise SynthesisSchemaError(f"{where}.confidence must be a number")
    if not 0.0 <= confidence <= 1.0:
        raise SynthesisSchemaError(f"{where}.confidence must be between 0 and 1")
    return Insight(
        title=_text(obj["title"], f"{where}.title"),
        pattern_type=_enum(PatternType, obj["pattern_type"], f"{where}.pattern_type"),
        statement=_text(obj["statement"], f"{where}.statement"),
        observations=observations,
        explanation=_text(obj["explanation"], f"{where}.explanation"),
        confidence=float(confidence),
        confidence_rationale=_text(obj["confidence_rationale"], f"{where}.confidence_rationale"),
        counter_evidence=tuple(
            _evidence(e, f"{where}.counter_evidence[{n}]")
            for n, e in enumerate(_list(obj["counter_evidence"], f"{where}.counter_evidence"))
        ),
        limitations=_text(obj["limitations"], f"{where}.limitations"),
    )


def _evidence_dict(e: EvidenceRef) -> dict[str, str]:
    return {"ref": e.ref, "field": e.field.value, "quote": e.quote}


def _insight_dict(i: Insight) -> dict[str, Any]:
    return {
        "title": i.title,
        "pattern_type": i.pattern_type.value,
        "statement": i.statement,
        "observations": [
            {"text": o.text, "evidence": [_evidence_dict(e) for e in o.evidence]}
            for o in i.observations
        ],
        "explanation": i.explanation,
        "confidence": i.confidence,
        "confidence_rationale": i.confidence_rationale,
        "counter_evidence": [_evidence_dict(e) for e in i.counter_evidence],
        "limitations": i.limitations,
    }
