"""Deterministic production input pack: exactly what the production model is
shown, built from one persisted Opportunity, its supporting research insights,
and a product profile.

Pure: takes already-loaded rows and a `ProductProfile`, returns a structured
pack. No I/O, no LLM, no database.

Mirrors `research.opportunity_pack`: the model sees only opaque references (P#
for a product capability), never database ids. The opportunity and its
research context are supplied as read-only, uncited background - Production
does not ask the model to cite insight references the way Opportunity
Creation does (see `research.production_prompt`: "research_context is
supporting background only ... not an invitation to cite it").
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from research.product_context import ProductProfile

PRODUCTION_PACK_VERSION = "production-pack-v1"


@dataclass(frozen=True)
class ProductionInputPack:
    prompt: dict[str, Any]  # exactly what the model is shown
    capability_refs: Mapping[str, str]  # "P#" -> product capability key
    manifest: dict[str, Any]  # what was selected; stored with the run
    product_key: str
    product_version: str

    def prompt_json(self) -> str:
        return json.dumps(self.prompt, ensure_ascii=False, indent=1)


def build_production_pack(
    *,
    opportunity: Mapping[str, Any],  # an `opportunities` row
    research_context: Sequence[Mapping[str, Any]],  # the insights it cites, read-only
    product: ProductProfile,
) -> ProductionInputPack:
    """Build the pack. Every opportunity is enough to attempt a production - there
    is no "insufficient input" threshold the way there is for research/opportunity
    creation, so this never raises."""
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

    opportunity_prompt = {
        "title": opportunity.get("title"),
        "audience": opportunity.get("audience"),
        "problem": opportunity.get("problem"),
        "opportunity": opportunity.get("opportunity"),
        "angle": opportunity.get("angle"),
        "marketing_objective": opportunity.get("marketing_objective"),
        "suggested_formats": opportunity.get("suggested_formats"),
        "cta_direction": opportunity.get("cta_direction"),
        "limitations": opportunity.get("limitations"),
    }
    research_prompt = [
        {
            "title": row.get("title"),
            "statement": row.get("statement"),
            "limitations": row.get("limitations"),
        }
        for row in research_context
    ]

    prompt = {
        "pack_version": PRODUCTION_PACK_VERSION,
        "opportunity": opportunity_prompt,
        "research_context": research_prompt,
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
        "pack_version": PRODUCTION_PACK_VERSION,
        "opportunity_id": str(opportunity["id"]),
        "product_key": product.key,
        "product_version": product.version,
        "product_profile": product.to_manifest(),
        "research_context_insight_ids": [str(r["id"]) for r in research_context if r.get("id") is not None],
        "product_capability_keys": [c.key for c in product.capabilities],
    }

    return ProductionInputPack(
        prompt=prompt,
        capability_refs={ref: key for key, ref in cref.items()},
        manifest=manifest,
        product_key=product.key,
        product_version=product.version,
    )
