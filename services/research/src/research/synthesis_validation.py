"""Are the synthesis model's claims supported by the evidence it was shown?

`SynthesisOutput.from_dict` checks the SHAPE. This checks the SUBSTANCE, in
application code, and never trusts the model:

* every ref exists in the pack (unknown refs are rejected),
* the field is valid for that ref and has supplied text,
* every quote is a verbatim substring of the supplied source text (after a
  documented whitespace normalisation) - never repaired,
* recurring patterns have at least two distinct content items,
* pattern-specific requirements (comments, statistics),
* numbers in the model's prose come from the pack or from counts we computed,
* the claim is scoped to the dataset and contains no advice,
* classification is used honestly (see below).

Classification is enrichment and covers only part of the dataset, so how a
claim relates to it matters:

  A. classification-DEPENDENT: the raw evidence (content, comments) cited does
     not by itself reach two distinct content items, so the pattern exists only
     because of classifications. It is bounded by classification coverage and
     its statement must say so ("among the classified videos ...").
  B. raw-evidence claim with SUPPLEMENTARY classification: the raw evidence alone
     already shows the pattern in two or more content items. Citing a
     classification as extra support does not make it classification-dependent
     and does not force "classified" into the statement, but the insight must
     acknowledge the incomplete coverage (limitations, rationale or
     explanation).
  C. unsupported generalisation: a majority-type claim ("most", "predominantly",
     ...) needs evidence covering more than half of its population, and at
     least two items. This applies to both kinds above.

The last two are deterministic heuristics (regular expressions): a backstop,
not a proof. They can reject valid prose; they must never be relaxed to make a
model answer pass. Anything that fails rejects the whole run; nothing is
silently dropped or edited.

Support figures stored with an insight (distinct content, creators, comments,
share of the dataset) are computed here from the resolved evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from research.evidence_pack import EvidencePack, numbers_in
from research.synthesis import (
    STATS_REF,
    EvidenceField,
    EvidenceRef,
    Insight,
    PatternType,
    SynthesisOutput,
    SynthesisValidationError,
)

# Which fields may be cited for each kind of reference.
_FIELDS_BY_KIND: dict[str, frozenset[EvidenceField]] = {
    "content": frozenset(
        {EvidenceField.TITLE, EvidenceField.DESCRIPTION, EvidenceField.TRANSCRIPT, EvidenceField.METRICS}
    ),
    "interpretation": frozenset({EvidenceField.CLASSIFICATION}),
    "comment": frozenset({EvidenceField.COMMENT_TEXT}),
}

# Scope: a statement must say it concerns the analysed dataset.
_SCOPE_MARKERS = (
    "analysed", "analyzed", "in this dataset", "in this research", "in the research set",
    "in this sample", "among the", "across the",
)
# Generalisations beyond the dataset (checked on model-authored claims, not limitations).
_OVERREACH_RE = re.compile(
    r"\b(the market|the industry|all (?:sole traders|creators|videos|content|audiences?)|"
    r"every (?:sole trader|creator|video)|everyone|nobody|always|never|universally|"
    r"most (?:people|sole traders|users))\b",
    re.IGNORECASE,
)
# Majority-type quantifiers: "most", "predominantly", "typically" ... A claim like
# this asserts something about the population, not just about the examples cited.
_MAJORITY_RE = re.compile(
    r"\b(most|mostly|majority|predominant\w*|predominat\w*|overwhelming\w*|typical\w*|"
    r"generally|usually|commonly|dominant|dominat\w*|nearly all|almost all|the norm|prevalent)\b",
    re.IGNORECASE,
)
# What an acknowledgement of incomplete classification coverage looks like: it
# must mention classification AND say it is partial (a word or a figure).
_COVERAGE_LIMIT_RE = re.compile(
    r"\b(only|partial\w*|incomplete|limited|subset|not all|some|unclassified|\d+\s+of\s+\d+)\b|\d+\s*%",
    re.IGNORECASE,
)
# Advice / recommendations (marketing action belongs to opportunities).
_ADVICE_RE = re.compile(
    r"\b(should|ought to|recommend\w*|advis\w*|consider|could benefit|"
    r"make more|create more|try (?:making|creating|using))\b",
    re.IGNORECASE,
)


def normalize_whitespace(text: str) -> str:
    """The one normalisation applied before comparing a quote with its source:
    every run of Unicode whitespace becomes a single space, ends trimmed. Case,
    punctuation and spelling are compared exactly."""
    return " ".join(text.split())


@dataclass(frozen=True)
class EvidenceRow:
    """One `insight_evidence` row, with real database ids."""

    role: str  # "supports" | "counter"
    observation_index: int | None
    content_id: str | None
    comment_id: str | None
    interpretation_id: str | None
    field: str
    quote: str  # the verified quote, whitespace-normalised


@dataclass(frozen=True)
class ValidatedInsight:
    insight: Insight
    evidence: tuple[EvidenceRow, ...]
    support: dict[str, Any]  # computed here, never by the model


@dataclass(frozen=True)
class ValidatedSynthesis:
    insights: tuple[ValidatedInsight, ...]
    no_insights_reason: str | None


def validate_synthesis(output: SynthesisOutput, pack: EvidencePack) -> ValidatedSynthesis:
    """Validate against the pack or raise SynthesisValidationError."""
    validated = tuple(
        _validate_insight(insight, n, pack) for n, insight in enumerate(output.insights)
    )
    return ValidatedSynthesis(insights=validated, no_insights_reason=output.no_insights_reason)


# --- one reference ----------------------------------------------------------------


def _resolve(ev: EvidenceRef, pack: EvidencePack, where: str, n: int) -> str:
    """Verify the ref/field/quote; return the normalised, verified quote."""
    if ev.ref == STATS_REF:
        if ev.field is not EvidenceField.STATISTICS:
            raise SynthesisValidationError(f"{where}: STATS can only be cited with field 'statistics'", n)
    else:
        target = pack.refs.get(ev.ref)
        if target is None:
            raise SynthesisValidationError(f"{where}: unknown reference {ev.ref!r} (not in the evidence pack)", n)
        if ev.field not in _FIELDS_BY_KIND[target.kind]:
            raise SynthesisValidationError(
                f"{where}: field {ev.field.value!r} cannot be cited from {ev.ref} ({target.kind})", n
            )
    source = pack.sources.get((ev.ref, ev.field.value))
    if source is None:
        raise SynthesisValidationError(
            f"{where}: {ev.ref} has no supplied {ev.field.value!r} text to quote", n
        )
    quote = normalize_whitespace(ev.quote)
    if not quote:
        raise SynthesisValidationError(f"{where}: blank quote", n)
    if "..." in quote or "…" in quote:
        raise SynthesisValidationError(f"{where}: quote contains an ellipsis; quotes must be verbatim", n)
    if quote not in normalize_whitespace(source):
        raise SynthesisValidationError(
            f"{where}: quote is not verbatim text of {ev.ref}.{ev.field.value}: {quote[:80]!r}", n
        )
    return quote


def _row(role: str, index: int | None, ev: EvidenceRef, quote: str, pack: EvidencePack) -> EvidenceRow:
    target = pack.refs[ev.ref]
    return EvidenceRow(
        role=role,
        observation_index=index,
        content_id=target.id if target.kind == "content" else None,
        comment_id=target.id if target.kind == "comment" else None,
        interpretation_id=target.id if target.kind == "interpretation" else None,
        field=ev.field.value,
        quote=quote,
    )


# --- one insight ------------------------------------------------------------------


def _validate_insight(insight: Insight, n: int, pack: EvidencePack) -> ValidatedInsight:
    rows: list[EvidenceRow] = []
    seen: set[tuple[Any, ...]] = set()
    supporting_refs: list[str] = []
    statistics_cited: list[str] = []

    for o_index, observation in enumerate(insight.observations):
        for e_index, ev in enumerate(observation.evidence):
            where = f"observations[{o_index}].evidence[{e_index}]"
            quote = _resolve(ev, pack, where, n)
            if ev.ref == STATS_REF:
                statistics_cited.append(quote)
                supporting_refs.append(ev.ref)
                continue
            row = _row("supports", o_index, ev, quote, pack)
            key = (row.role, row.observation_index, row.content_id, row.comment_id,
                   row.interpretation_id, row.field, row.quote)
            if key not in seen:
                seen.add(key)
                rows.append(row)
            supporting_refs.append(ev.ref)

    for e_index, ev in enumerate(insight.counter_evidence):
        quote = _resolve(ev, pack, f"counter_evidence[{e_index}]", n)
        if ev.ref == STATS_REF:
            continue  # statistics carry no database target; not stored as a row
        row = _row("counter", None, ev, quote, pack)
        key = (row.role, row.observation_index, row.content_id, row.comment_id,
               row.interpretation_id, row.field, row.quote)
        if key not in seen:
            seen.add(key)
            rows.append(row)

    target_refs = [r for r in supporting_refs if r != STATS_REF]
    if not target_refs:
        raise SynthesisValidationError("no supporting evidence from content, classifications or comments", n)

    kinds = {pack.refs[r].kind for r in target_refs}
    content_ids = {pack.refs[r].content_id for r in target_refs}
    comment_refs = {r for r in target_refs if pack.refs[r].kind == "comment"}
    interpretation_refs = {r for r in target_refs if pack.refs[r].kind == "interpretation"}
    creators = {pack.creator_by_content_id.get(c) for c in content_ids} - {None}

    # A recurring pattern needs at least two DIFFERENT content items. Comments and
    # classifications count for the content item they belong to.
    if len(content_ids) < 2:
        raise SynthesisValidationError(
            f"a {insight.pattern_type.value} needs evidence from at least two distinct "
            f"content items; found {len(content_ids)}", n)
    if insight.pattern_type is PatternType.CONTENT_COMMENT_PATTERN:
        if "comment" not in kinds or not kinds & {"content", "interpretation"}:
            raise SynthesisValidationError(
                "a content_comment_pattern must cite both content-side evidence (C#/I#) and comments (M#)", n)
    if insight.pattern_type is PatternType.PERFORMANCE_SIGNAL and not statistics_cited:
        raise SynthesisValidationError(
            "a performance_signal must cite the computed statistics (ref STATS)", n)

    support: dict[str, Any] = {
        "content_count": len(content_ids),
        "distinct_creators": len(creators),
        "comment_count": len(comment_refs),
        "interpretation_count": len(interpretation_refs),
        "content_total": pack.content_total,
        "share_of_content": round(len(content_ids) / pack.content_total, 4),
        "classification_coverage": pack.classification_coverage,
        "classified_content_count": pack.classified_content_count,
        "counter_evidence_count": sum(1 for r in rows if r.role == "counter"),
        "statistics_cited": statistics_cited,
        "computed_by": "code",
    }

    # What the RAW evidence alone establishes: content and comments (a comment
    # counts for the content item it is on), excluding classifications.
    raw_content_ids = {
        pack.refs[r].content_id for r in target_refs if pack.refs[r].kind in ("content", "comment")
    }
    _check_prose(
        insight, n, pack, support,
        interpretation_refs=interpretation_refs,
        raw_content_ids=raw_content_ids,
        content_ids=content_ids,
    )
    return ValidatedInsight(insight=insight, evidence=tuple(rows), support=support)


# --- prose rules ------------------------------------------------------------------


def _claims(insight: Insight) -> list[str]:
    """Model-authored claim text (limitations and rationale are exempt from overreach/advice)."""
    return [insight.title, insight.statement, insight.explanation] + [
        o.text for o in insight.observations
    ]


def _check_prose(
    insight: Insight,
    n: int,
    pack: EvidencePack,
    support: dict[str, Any],
    *,
    interpretation_refs: set[str],
    raw_content_ids: set[str],
    content_ids: set[str],
) -> None:
    claims = _claims(insight)

    if match := next((m for c in claims if (m := _ADVICE_RE.search(c))), None):
        raise SynthesisValidationError(
            f"marketing advice is out of scope for synthesis (found {match.group(0)!r})", n)
    if match := next((m for c in claims if (m := _OVERREACH_RE.search(c))), None):
        raise SynthesisValidationError(
            f"claim exceeds the dataset scope (found {match.group(0)!r})", n)

    statement = insight.statement.lower()
    if not any(marker in statement for marker in _SCOPE_MARKERS):
        raise SynthesisValidationError(
            "the statement must be scoped to the analysed dataset (e.g. 'Among the analysed videos ...')", n)

    cites_classification = bool(interpretation_refs)
    partial = pack.classification_coverage < 1
    # A: the raw evidence alone does not reach two content items, so the pattern
    # exists only because of classifications. B otherwise (see module docstring).
    classification_dependent = cites_classification and len(raw_content_ids) < 2

    # C: majority-type claims must be backed by a majority of their population.
    if match := next(
        (m for c in (insight.title, insight.statement) if (m := _MAJORITY_RE.search(c))), None
    ):
        if classification_dependent:
            supporting, population, of_what = len(interpretation_refs), pack.classified_content_count, "classified videos"
        else:
            supporting, population, of_what = len(content_ids), pack.content_total, "videos analysed"
        if supporting < 2 or supporting * 2 <= population:
            raise SynthesisValidationError(
                f"a majority claim ({match.group(0)!r}) needs evidence covering more than half of "
                f"the {of_what} and at least two items; the cited evidence covers {supporting} of "
                f"{population}", n)

    if classification_dependent and partial and "classified" not in statement:
        raise SynthesisValidationError(
            "classification is incomplete; a statement resting on classifications must say "
            "'classified' (e.g. 'among the classified videos')", n)
    if cites_classification and partial and not classification_dependent:
        acknowledgement = " ".join(
            (insight.limitations, insight.confidence_rationale, insight.explanation)
        ).lower()
        if "classif" not in acknowledgement or not _COVERAGE_LIMIT_RE.search(acknowledgement):
            raise SynthesisValidationError(
                f"this insight cites a classification but only {pack.classified_content_count} of "
                f"{pack.content_total} content items are classified; its limitations, rationale or "
                "explanation must acknowledge the incomplete classification coverage", n)

    allowed = _allowed_numbers(pack, support)
    for text in claims + [insight.limitations, insight.confidence_rationale]:
        for number in numbers_in(text):
            if not _is_allowed(number, allowed):
                raise SynthesisValidationError(
                    f"numeric claim {number:g} does not come from the evidence pack or from "
                    "counts computed for this insight", n)


def _allowed_numbers(pack: EvidencePack, support: dict[str, Any]) -> set[float]:
    allowed = set(pack.numbers)
    for key in ("content_count", "distinct_creators", "comment_count", "interpretation_count",
                "content_total", "classified_content_count", "counter_evidence_count"):
        allowed.add(float(support[key]))
    allowed.add(float(support["share_of_content"]))
    allowed.add(round(support["share_of_content"] * 100, 1))
    return allowed


def _is_allowed(number: float, allowed: set[float]) -> bool:
    if number in allowed:
        return True
    for a in allowed:
        if 0 < a <= 1:  # a proportion: it may be written as a percentage or rounded
            if abs(number - a * 100) <= 0.5 or abs(number - round(a, 2)) < 1e-9:
                return True
    return False
