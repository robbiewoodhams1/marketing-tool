"""Deterministic opportunity input pack: exactly what the opportunity model is
shown, built from one synthesis's stored insights and a product profile.

Pure: takes already-loaded rows and a `ProductProfile`, returns a structured
pack. No I/O, no LLM, no database.

Mirrors `research.evidence_pack`: the model sees only opaque references (N#
for a research insight, P# for a product capability), never database ids or
raw product prose it could misquote; the application maps references back
after the response and rejects any it did not supply
(`research.opportunity_validation`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from research.product_context import ProductProfile

OPPORTUNITY_PACK_VERSION = "opportunity-pack-v1"

# Fewer than one insight means there is nothing to translate into an
# opportunity. No LLM call is made below this.
MIN_INSIGHTS_FOR_OPPORTUNITIES = 1


class OpportunityPackError(Exception):
    pass


class InsufficientResearchError(OpportunityPackError):
    """Too little research to attempt opportunity creation. No LLM call should be made."""


@dataclass(frozen=True)
class OpportunityInputPack:
    prompt: dict[str, Any]  # exactly what the model is shown
    insight_refs: Mapping[str, str]  # "N#" -> insight id
    capability_refs: Mapping[str, str]  # "P#" -> product capability key
    manifest: dict[str, Any]  # what was selected; stored with the run
    insight_ids: tuple[str, ...]
    product_key: str
    product_version: str

    def prompt_json(self) -> str:
        return json.dumps(self.prompt, ensure_ascii=False, indent=1)


def build_opportunity_pack(
    *,
    job: Mapping[str, Any],
    synthesis: Mapping[str, Any],  # {"id": ..., "research_job_id": ...}
    insight_rows: Sequence[Mapping[str, Any]],
    product: ProductProfile,
) -> OpportunityInputPack:
    """Build the pack, or raise InsufficientResearchError."""
    if len(insight_rows) < MIN_INSIGHTS_FOR_OPPORTUNITIES:
        raise InsufficientResearchError(
            f"{len(insight_rows)} insight(s) from this synthesis; opportunity creation needs at "
            f"least {MIN_INSIGHTS_FOR_OPPORTUNITIES} to work from"
        )

    insights = sorted(insight_rows, key=lambda r: str(r["id"]))
    iref = {str(row["id"]): f"N{n}" for n, row in enumerate(insights, start=1)}

    insight_prompt: list[dict[str, Any]] = []
    manifest_insights: list[dict[str, Any]] = []
    for row in insights:
        rid = str(row["id"])
        ref = iref[rid]
        insight_prompt.append({
            "ref": ref,
            "title": row.get("title"),
            "statement": row.get("statement"),
            "pattern_type": row.get("pattern_type"),
            "explanation": row.get("explanation"),
            "model_confidence": row.get("model_confidence"),
            "confidence_rationale": row.get("confidence_rationale"),
            "limitations": row.get("limitations"),
            "support": row.get("support") or {},
        })
        manifest_insights.append({"ref": ref, "id": rid})

    cref = {c.key: f"P{n}" for n, c in enumerate(product.capabilities, start=1)}
    capability_prompt = [
        {
            "ref": cref[c.key],
            "name": c.name,
            "description": c.description,
            "problem_solved": c.problem_solved,
        }
        for c in product.capabilities
    ]

    prompt = {
        "pack_version": OPPORTUNITY_PACK_VERSION,
        "research_job": {
            "query": job.get("query"), "audience": job.get("audience"), "objective": job.get("objective"),
        },
        "research_insights": insight_prompt,
        "product": {
            "key": product.key,
            "name": product.name,
            "description": product.description,
            "audience": list(product.audience),
            "capabilities": capability_prompt,
            "differentiators": list(product.differentiators),
            "positioning": product.positioning,
            "limitations": list(product.limitations),
            "pricing": product.pricing,
        },
    }

    manifest = {
        "pack_version": OPPORTUNITY_PACK_VERSION,
        "synthesis_id": str(synthesis["id"]),
        "research_job_id": str(synthesis["research_job_id"]),
        "product_key": product.key,
        "product_version": product.version,
        "product_profile": product.to_manifest(),
        "selected_insight_ids": [m["id"] for m in manifest_insights],
        "insights": manifest_insights,
        "product_capability_keys": [c.key for c in product.capabilities],
    }

    return OpportunityInputPack(
        prompt=prompt,
        insight_refs={ref: rid for rid, ref in iref.items()},
        capability_refs={ref: key for key, ref in cref.items()},
        manifest=manifest,
        insight_ids=tuple(str(row["id"]) for row in insights),
        product_key=product.key,
        product_version=product.version,
    )
