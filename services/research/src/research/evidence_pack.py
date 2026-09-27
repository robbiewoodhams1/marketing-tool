"""Deterministic evidence pack: exactly what the synthesis model is shown.

Pure: takes already-loaded rows, returns a structured pack. No I/O.

* The model sees opaque references (C1.. content, I1.. classification
  interpretations, M1.. comments), never database ids. The application maps
  references back after the response and rejects any it did not supply.
* NULL metrics stay `null` ("unavailable"); they are never turned into 0 and
  never enter computed statistics.
* Every number in the statistics block is computed here, never by the model.
* Bounds are fixed and deterministic. If the pack is too large it degrades along
  a fixed ladder (comments, then transcript/description excerpts, ...) and the
  manifest records exactly what the model saw and what was cut. Content items
  are never dropped; if even the smallest level does not fit, that is an error.
* Classification is enrichment, not a prerequisite. Unclassified content is
  included as raw evidence and the coverage gap is recorded and disclosed.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from research.classification import ClassificationResult, ClassificationSchemaError

PACK_VERSION = "pack-v1"

# Fewer than two content items cannot show a *recurring* pattern, which is what
# synthesis looks for. Below this no LLM call is made.
MIN_CONTENT_FOR_SYNTHESIS = 2

CLASSIFICATION_ANALYSIS_TYPE = "classification"
METRIC_NAMES = ("views", "likes", "comments_count", "shares", "saves")
CLASSIFICATION_FIELD_ORDER = (
    "topic", "audience", "pain_point", "hook", "hook_type", "format", "emotion", "cta",
)
COUNTED_CLASSIFICATION_FIELDS = ("hook_type", "format", "emotion")  # controlled vocabularies

# Excerpt rule (documented in the manifest): the OPENING of the text, cut at a
# fixed character count. The opening matters most for hook analysis; no other
# part of a transcript is ever included in v1.
EXCERPT_RULE = "opening: first N characters, hard cut, no other part of the text"
COMMENT_RULE = (
    "per content item: highest likes first (null likes last), then external id; "
    "first N comments; blank comments skipped"
)
CONTENT_RULE = "all content items, ordered by published_at, external_id, id"
CLASSIFICATION_RULE = (
    "latest classification interpretation per content (created_at desc, id desc); "
    "no fallback to older ones; malformed results are unusable and recorded"
)

# Rough size guard: ~4 characters per token, so this is on the order of 60k tokens.
DEFAULT_BUDGET_CHARS = 240_000

_NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


class EvidencePackError(Exception):
    pass


class InsufficientEvidenceError(EvidencePackError):
    """Too little evidence to attempt a synthesis. No LLM call should be made."""


class EvidenceTooLargeError(EvidencePackError):
    """The pack does not fit even at the smallest bounds level."""


@dataclass(frozen=True)
class PackLevel:
    transcript_chars: int
    description_chars: int
    comments_per_content: int
    comment_chars: int


# Fixed degradation ladder, most detail first. The first level whose serialised
# pack fits the budget is used.
DEFAULT_LADDER: tuple[PackLevel, ...] = (
    PackLevel(3000, 800, 20, 500),
    PackLevel(3000, 800, 10, 400),
    PackLevel(1500, 500, 10, 300),
    PackLevel(1500, 500, 5, 300),
    PackLevel(800, 300, 3, 200),
    PackLevel(800, 300, 0, 0),
)


@dataclass(frozen=True)
class PackBounds:
    budget_chars: int = DEFAULT_BUDGET_CHARS
    ladder: tuple[PackLevel, ...] = DEFAULT_LADDER


@dataclass(frozen=True)
class RefTarget:
    """What a reference points at in the database."""

    kind: str  # "content" | "interpretation" | "comment"
    id: str  # id of the referenced row
    content_id: str  # the content item it belongs to (itself, for kind == "content")


@dataclass(frozen=True)
class EvidencePack:
    prompt: dict[str, Any]  # exactly what the model is shown
    refs: Mapping[str, RefTarget]
    sources: Mapping[tuple[str, str], str]  # (ref, field) -> text exactly as supplied
    manifest: dict[str, Any]  # what was selected/cut; stored with the run
    creator_by_content_id: Mapping[str, str | None]
    numbers: frozenset[float]  # every number visible in the prompt
    content_total: int
    classified_content_count: int
    classification_coverage: float
    content_ids: tuple[str, ...]
    interpretation_ids: tuple[str, ...]
    comment_ids: tuple[str, ...]
    bounds: dict[str, Any] = field(default_factory=dict)

    def prompt_json(self) -> str:
        return json.dumps(self.prompt, ensure_ascii=False, indent=1)


# --- small helpers ------------------------------------------------------------


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    return int(value) if isinstance(value, (int, float)) else None


def _parse_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _excerpt(text: Any, limit: int) -> dict[str, Any] | None:
    """Opening excerpt object, or None if there is no text."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    cut = text[:limit].rstrip() if len(text) > limit else text
    return {"text": cut, "truncated": len(text) > limit, "chars_total": len(text)}


def numbers_in(text: str) -> set[float]:
    """Numbers mentioned in free text (commas removed). Ref labels like C1 are not numbers."""
    out: set[float] = set()
    for match in _NUMBER_RE.findall(text):
        try:
            out.add(float(match.replace(",", "")))
        except ValueError:
            continue
    return out


def _collect_numbers(node: Any, out: set[float]) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        out.add(float(node))
    elif isinstance(node, str):
        out |= numbers_in(node)
    elif isinstance(node, Mapping):
        for value in node.values():
            _collect_numbers(value, out)
    elif isinstance(node, Sequence):
        for value in node:
            _collect_numbers(value, out)


def _summary(values: list[float | int]) -> dict[str, Any]:
    """min/max/median over the values that exist; n_available is always present."""
    if not values:
        return {"available": 0, "min": None, "max": None, "median": None}
    median = statistics.median(values)
    return {
        "available": len(values),
        "min": min(values),
        "max": max(values),
        "median": round(median, 4) if isinstance(median, float) else median,
    }


def _render(lines: dict[str, Any]) -> str:
    return "\n".join(
        f"{k}: {'unavailable' if v is None else v}" for k, v in lines.items()
    )


def _flatten(prefix: str, node: Any, out: dict[str, Any]) -> None:
    if isinstance(node, Mapping):
        for key in sorted(node):
            _flatten(f"{prefix}.{key}" if prefix else str(key), node[key], out)
    else:
        out[prefix] = node


# --- classification selection -------------------------------------------------


def _latest_classifications(
    interpretation_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """content_id -> latest classification interpretation row (newest, then id)."""
    latest: dict[str, Mapping[str, Any]] = {}
    for row in interpretation_rows:
        if row.get("analysis_type") != CLASSIFICATION_ANALYSIS_TYPE:
            continue
        cid = row["content_id"]
        key = (_parse_time(row.get("created_at")), str(row["id"]))
        current = latest.get(cid)
        if current is None or key > (_parse_time(current.get("created_at")), str(current["id"])):
            latest[cid] = row
    return latest


def _usable(row: Mapping[str, Any]) -> ClassificationResult | None:
    try:
        return ClassificationResult.from_dict(row.get("result"))
    except ClassificationSchemaError:
        return None


# --- the builder ----------------------------------------------------------------


def build_evidence_pack(
    *,
    job: Mapping[str, Any],
    content_rows: Sequence[Mapping[str, Any]],
    comment_rows: Sequence[Mapping[str, Any]],
    interpretation_rows: Sequence[Mapping[str, Any]],
    bounds: PackBounds = PackBounds(),
) -> EvidencePack:
    """Build the pack, or raise InsufficientEvidenceError / EvidenceTooLargeError."""
    if len(content_rows) < MIN_CONTENT_FOR_SYNTHESIS:
        raise InsufficientEvidenceError(
            f"{len(content_rows)} content item(s); synthesis needs at least "
            f"{MIN_CONTENT_FOR_SYNTHESIS} to look for a recurring pattern"
        )

    content = sorted(
        content_rows,
        key=lambda r: (str(r.get("published_at") or ""), str(r.get("external_id") or ""), str(r["id"])),
    )
    cref = {row["id"]: f"C{n}" for n, row in enumerate(content, start=1)}

    latest = _latest_classifications(interpretation_rows)
    classifications: dict[str, tuple[Mapping[str, Any], ClassificationResult]] = {}
    unusable: list[str] = []
    for row in content:
        interpretation = latest.get(row["id"])
        if interpretation is None:
            continue
        parsed = _usable(interpretation)
        if parsed is None:
            unusable.append(str(interpretation["id"]))
        else:
            classifications[row["id"]] = (interpretation, parsed)

    comments_by_content: dict[str, list[Mapping[str, Any]]] = {}
    for row in comment_rows:
        text = row.get("text")
        if row.get("content_id") in cref and isinstance(text, str) and text.strip():
            comments_by_content.setdefault(row["content_id"], []).append(row)
    for rows in comments_by_content.values():
        rows.sort(
            key=lambda r: (
                -(r["likes"] if isinstance(r.get("likes"), int) else -1),
                str(r.get("external_id") or ""),
                str(r["id"]),
            )
        )
    comments_available = sum(len(v) for v in comments_by_content.values())

    stats = _statistics(content, classifications, comments_available)
    classified = len(classifications)
    total = len(content)
    coverage = round(classified / total, 4)

    tried: list[dict[str, Any]] = []
    for index, level in enumerate(bounds.ladder):
        pack = _build_at_level(
            job, content, cref, classifications, comments_by_content, comments_available,
            unusable, stats, classified, total, coverage, level, index, bounds, tried,
        )
        size = len(pack.prompt_json())
        fits = size <= bounds.budget_chars
        tried.append({"level": index, "chars": size, "fits": fits})
        if fits:
            pack.manifest["bounds"]["measured_chars"] = size
            pack.manifest["bounds"]["levels_tried"] = tried
            return pack
    raise EvidenceTooLargeError(
        f"evidence pack exceeds {bounds.budget_chars} characters even at the smallest "
        f"level ({tried[-1]['chars']}); content items are never dropped silently"
    )


def _statistics(
    content: Sequence[Mapping[str, Any]],
    classifications: Mapping[str, tuple[Mapping[str, Any], ClassificationResult]],
    comments_available: int,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for name in METRIC_NAMES:
        values = [v for r in content if (v := _int_or_none(r.get(name))) is not None]
        metrics[name] = _summary(values)
    like_rates = [
        r["likes"] / r["views"]
        for r in content
        if _int_or_none(r.get("views")) and _int_or_none(r.get("likes")) is not None
    ]
    like_rate = _summary([round(x, 4) for x in like_rates])
    counts: dict[str, dict[str, int]] = {}
    for fname in COUNTED_CLASSIFICATION_FIELDS:
        counter = Counter(
            getattr(parsed, fname).value
            for _, parsed in classifications.values()
            if getattr(parsed, fname).value is not None
        )
        counts[fname] = dict(sorted(counter.items()))
    creators = {r.get("creator") for r in content if r.get("creator")}
    return {
        "content_count": len(content),
        "distinct_creators": len(creators),
        "metrics": metrics,
        "like_rate": like_rate,
        "classification": {
            "classified_content_count": len(classifications),
            "coverage": round(len(classifications) / len(content), 4),
            "value_counts_among_classified": counts,
        },
        "comments": {"available": comments_available},
    }


def _build_at_level(
    job, content, cref, classifications, comments_by_content, comments_available,
    unusable, stats, classified, total, coverage, level, level_index, bounds, tried,
) -> EvidencePack:
    refs: dict[str, RefTarget] = {}
    sources: dict[tuple[str, str], str] = {}
    creators: dict[str, str | None] = {}
    content_prompt: list[dict[str, Any]] = []
    manifest_content: list[dict[str, Any]] = []
    manifest_interpretations: list[dict[str, Any]] = []
    interpretation_ids: list[str] = []
    comment_prompt: list[dict[str, Any]] = []
    manifest_comments: list[dict[str, Any]] = []
    comment_ids: list[str] = []
    unclassified_refs: list[str] = []

    view_median = stats["metrics"]["views"]["median"]
    i_counter = 0
    m_counter = 0
    transcripts_truncated = descriptions_truncated = 0

    for row in content:
        ref = cref[row["id"]]
        cid = row["id"]
        refs[ref] = RefTarget("content", cid, cid)
        creators[cid] = row.get("creator")

        raw = {name: _int_or_none(row.get(name)) for name in METRIC_NAMES}  # None stays None
        like_rate = (
            round(raw["likes"] / raw["views"], 4)
            if raw["views"] and raw["likes"] is not None
            else None
        )
        views_vs_median = (
            round(raw["views"] / view_median, 4)
            if raw["views"] is not None and view_median
            else None
        )
        computed = {"like_rate": like_rate, "views_vs_dataset_median": views_vs_median}
        metrics_text = _render({**raw, **{k: v for k, v in computed.items() if v is not None}})
        transcript = _excerpt(row.get("transcript"), level.transcript_chars)
        description = _excerpt(row.get("description"), level.description_chars)
        transcripts_truncated += bool(transcript and transcript["truncated"])
        descriptions_truncated += bool(description and description["truncated"])

        title = row.get("title") if isinstance(row.get("title"), str) and row["title"].strip() else None
        if title:
            sources[(ref, "title")] = title.strip()
        if description:
            sources[(ref, "description")] = description["text"]
        if transcript:
            sources[(ref, "transcript")] = transcript["text"]
        sources[(ref, "metrics")] = metrics_text

        classification_prompt: dict[str, Any] | None = None
        interpretation_id: str | None = None
        if cid in classifications:
            interpretation, parsed = classifications[cid]
            i_counter += 1
            iref = f"I{i_counter}"
            interpretation_id = str(interpretation["id"])
            refs[iref] = RefTarget("interpretation", interpretation_id, cid)
            interpretation_ids.append(interpretation_id)
            fields = {
                name: {
                    "value": getattr(parsed, name).value,
                    "confidence": getattr(parsed, name).confidence,
                    "evidence": getattr(parsed, name).evidence,
                }
                for name in CLASSIFICATION_FIELD_ORDER
            }
            text = "\n".join(
                f"{name}: {f['value'] if f['value'] is not None else 'unavailable'}"
                + (f" | confidence {f['confidence']} | evidence: {f['evidence']}" if f["value"] is not None else "")
                for name, f in fields.items()
            )
            sources[(iref, "classification")] = text
            classification_prompt = {
                "ref": iref,
                "model": interpretation.get("model"),
                "prompt_version": interpretation.get("prompt_version"),
                "schema_version": interpretation.get("schema_version"),
                "fields": fields,
                "classification_text": text,
            }
            manifest_interpretations.append({
                "ref": iref, "id": interpretation_id, "content_ref": ref,
                "model": interpretation.get("model"),
                "prompt_version": interpretation.get("prompt_version"),
                "schema_version": interpretation.get("schema_version"),
            })
        else:
            unclassified_refs.append(ref)

        shown = comments_by_content.get(cid, [])[: level.comments_per_content]
        for crow in shown:
            m_counter += 1
            mref = f"M{m_counter}"
            refs[mref] = RefTarget("comment", str(crow["id"]), cid)
            text = crow["text"].strip()[: level.comment_chars].rstrip()
            sources[(mref, "comment_text")] = text
            comment_ids.append(str(crow["id"]))
            comment_prompt.append({
                "ref": mref, "content_ref": ref, "text": text,
                "likes": _int_or_none(crow.get("likes")), "type": crow.get("type"),
            })
            manifest_comments.append({"ref": mref, "id": str(crow["id"]), "content_ref": ref})

        content_prompt.append({
            "ref": ref,
            "title": title,
            "creator": row.get("creator"),
            "url": row.get("url"),
            "published_at": row.get("published_at"),
            "raw_metrics": raw,
            "metrics_available": {k: v is not None for k, v in raw.items()},
            "computed_metrics": computed,
            "metrics_text": metrics_text,
            "description_excerpt": description,
            "transcript_excerpt": transcript,
            "classification": classification_prompt,
        })
        manifest_content.append({
            "ref": ref, "id": cid, "external_id": row.get("external_id"),
            "interpretation_id": interpretation_id,
            "title_available": title is not None,
            "description": _excerpt_manifest(description),
            "transcript": _excerpt_manifest(transcript),
            "comments_available": len(comments_by_content.get(cid, [])),
            "comments_selected": len(shown),
            "metrics_available": {k: v is not None for k, v in raw.items()},
        })

    flat: dict[str, Any] = {}
    _flatten("", stats, flat)
    statistics_text = _render(flat)
    sources[("STATS", "statistics")] = statistics_text

    coverage_block = {
        "content_total": total,
        "classified_content_count": classified,
        "classification_coverage": coverage,
        "unclassified_content_refs": unclassified_refs,
        "comments_available": comments_available,
        "comments_shown": len(comment_ids),
        "comment_selection": COMMENT_RULE,
        "notes": _coverage_notes(total, classified, comments_available, len(comment_ids)),
    }
    prompt = {
        "pack_version": PACK_VERSION,
        "research_job": {
            "query": job.get("query"), "audience": job.get("audience"),
            "objective": job.get("objective"),
        },
        "coverage": coverage_block,
        "statistics": stats,
        "statistics_text": statistics_text,
        "content": content_prompt,
        "comments": comment_prompt,
    }
    numbers: set[float] = set()
    _collect_numbers(prompt, numbers)

    level_dict = {
        "transcript_chars": level.transcript_chars,
        "description_chars": level.description_chars,
        "comments_per_content": level.comments_per_content,
        "comment_chars": level.comment_chars,
    }
    bounds_dict = {
        "budget_chars": bounds.budget_chars,
        "level_index": level_index,
        "level": level_dict,
        "ladder": [
            {"transcript_chars": lv.transcript_chars, "description_chars": lv.description_chars,
             "comments_per_content": lv.comments_per_content, "comment_chars": lv.comment_chars}
            for lv in bounds.ladder
        ],
    }
    manifest = {
        "pack_version": PACK_VERSION,
        # a copy: measurements added later must never leak into the run identity
        "bounds": dict(bounds_dict),
        "selection_rules": {
            "content": CONTENT_RULE, "transcript": EXCERPT_RULE, "description": EXCERPT_RULE,
            "comments": COMMENT_RULE, "classification": CLASSIFICATION_RULE,
        },
        "selected_content_ids": [c["id"] for c in manifest_content],
        "selected_interpretation_ids": interpretation_ids,
        "selected_comment_ids": comment_ids,
        "unusable_interpretation_ids": unusable,
        "content": manifest_content,
        "interpretations": manifest_interpretations,
        "comments": manifest_comments,
        "coverage": {k: coverage_block[k] for k in (
            "content_total", "classified_content_count", "classification_coverage",
            "comments_available", "comments_shown")},
        "truncation": {
            "transcripts_truncated": transcripts_truncated,
            "descriptions_truncated": descriptions_truncated,
            "comments_not_shown": comments_available - len(comment_ids),
            "level_index": level_index,
        },
    }
    return EvidencePack(
        prompt=prompt,
        refs=refs,
        sources=sources,
        manifest=manifest,
        creator_by_content_id=creators,
        numbers=frozenset(numbers),
        content_total=total,
        classified_content_count=classified,
        classification_coverage=coverage,
        content_ids=tuple(c["id"] for c in manifest_content),
        interpretation_ids=tuple(interpretation_ids),
        comment_ids=tuple(comment_ids),
        bounds=bounds_dict,
    )


def _excerpt_manifest(excerpt: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "available": excerpt is not None,
        "chars_total": excerpt["chars_total"] if excerpt else 0,
        "chars_included": len(excerpt["text"]) if excerpt else 0,
        "truncated": bool(excerpt and excerpt["truncated"]),
    }


def _coverage_notes(total: int, classified: int, available: int, shown: int) -> list[str]:
    notes = [
        "null / 'unavailable' means the metric was not measured; it is NOT zero.",
        "All numbers in `statistics` and `computed_metrics` were computed by code.",
    ]
    if classified < total:
        notes.append(
            f"Classification is INCOMPLETE: {classified} of {total} content items are "
            "classified. Do not generalise a classification-derived pattern to the "
            "whole dataset; scope it to the classified items."
        )
    if shown < available:
        notes.append(
            f"Only {shown} of {available} comments are shown (highest likes first per "
            "video), so the comments are biased toward popular comments."
        )
    return notes
