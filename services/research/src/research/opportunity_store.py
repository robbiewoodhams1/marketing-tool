"""Load a synthesis's insights and persist a validated opportunity-creation run.

Mirrors `research.synthesis_store`: writes go through the `Database` protocol
so tests never need Supabase. The run itself is stored by ONE call to the
Postgres function `save_opportunities` (a single transaction): a run row, its
opportunities and their insight-evidence rows all persist, or none do. The
function stores an already-validated payload; all validation happens in Python
(`research.opportunity_validation`).

`run_key` is UNIQUE in the database, so the database - not this code - is the
final guarantee that an identical run is never stored twice, including under
concurrent execution. `find_run` is only an optimisation that avoids paying for
an LLM call whose result would be discarded.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from research.logging import get_logger
from research.opportunity import (
    OPPORTUNITY_ANALYSIS_TYPE,
    OPPORTUNITY_SCHEMA_VERSION,
    OpportunityCreationOutput,
)
from research.opportunity_pack import OpportunityInputPack
from research.opportunity_prompt import PROMPT_VERSION
from research.opportunity_validation import ValidatedOpportunityCreation
from research.persistence import Database, PersistenceError, ResearchJob, ResearchRepository

log = get_logger("research.opportunity_store")

SYNTHESIS_COLUMNS = "id,research_job_id,analysis_type,model,prompt_version,schema_version,run_key,result"
INSIGHT_COLUMNS = (
    "id,research_job_id,synthesis_id,title,statement,pattern_type,model_confidence,"
    "confidence_rationale,explanation,limitations,observations,support"
)


@dataclass(frozen=True)
class SynthesisRecord:
    """An existing `syntheses` row, as the opportunity engine needs it."""

    id: str
    research_job_id: str
    model: str
    result: Mapping[str, Any]


@dataclass(frozen=True)
class SaveOutcome:
    status: str  # "created" | "exists"
    run_id: str
    opportunities: int


def compute_run_key(
    *,
    synthesis_id: str,
    research_job_id: str,
    model: str,
    pack: OpportunityInputPack,
    analysis_type: str = OPPORTUNITY_ANALYSIS_TYPE,
    prompt_version: str | None = None,
    schema_version: str | None = None,
) -> str:
    """SHA-256 of the canonical run configuration.

    Canonical = JSON with sorted keys, no insignificant whitespace, UTF-8, and
    every id list sorted. Anything that changes what the model saw, which
    product it saw, or how it was asked changes the key; nothing else does.
    """
    identity = {
        "analysis_type": analysis_type,
        "synthesis_id": synthesis_id,
        "research_job_id": research_job_id,
        "model": model,
        "prompt_version": prompt_version or PROMPT_VERSION,
        "schema_version": schema_version or OPPORTUNITY_SCHEMA_VERSION,
        "pack_version": pack.manifest["pack_version"],
        "product_key": pack.product_key,
        "product_version": pack.product_version,
        "selected_insight_ids": sorted(pack.insight_ids),
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_save_payload(
    *,
    research_job_id: str,
    synthesis_id: str,
    product_key: str,
    product_version: str,
    model: str,
    run_key: str,
    pack: OpportunityInputPack,
    output: OpportunityCreationOutput,
    validated: ValidatedOpportunityCreation,
) -> dict[str, Any]:
    """The exact payload handed to `save_opportunities`."""
    return {
        "run": {
            "research_job_id": research_job_id,
            "synthesis_id": synthesis_id,
            "product_key": product_key,
            "product_version": product_version,
            "analysis_type": OPPORTUNITY_ANALYSIS_TYPE,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "schema_version": OPPORTUNITY_SCHEMA_VERSION,
            "run_key": run_key,
            "input_manifest": pack.manifest,
            "result": output.to_dict(),
        },
        "opportunities": [
            {
                "title": v.opportunity.title,
                "audience": v.opportunity.audience,
                "problem": v.opportunity.problem,
                "opportunity": v.opportunity.opportunity,
                "angle": v.opportunity.angle,
                "marketing_objective": v.opportunity.marketing_objective.value,
                "suggested_formats": [f.value for f in v.opportunity.suggested_formats],
                "cta_direction": v.opportunity.cta_direction,
                "product_capability_refs": list(v.capability_keys),
                "confidence": v.opportunity.confidence,
                "limitations": v.opportunity.limitations,
                "evidence": [
                    {"insight_id": insight_id, "note": ref.note}
                    for insight_id, ref in zip(v.insight_ids, v.opportunity.research_evidence, strict=True)
                ],
            }
            for v in validated.opportunities
        ],
    }


class OpportunityRepository:
    def __init__(self, db: Database):
        self._db = db

    def get_synthesis(self, synthesis_id: str) -> SynthesisRecord | None:
        rows = self._db.select("syntheses", SYNTHESIS_COLUMNS, eq={"id": synthesis_id})
        if not rows:
            return None
        row = rows[0]
        return SynthesisRecord(
            id=str(row["id"]),
            research_job_id=str(row["research_job_id"]),
            model=row.get("model"),
            result=row.get("result") or {},
        )

    def get_job(self, job_id: str) -> ResearchJob | None:
        return ResearchRepository(self._db).get_job(job_id)

    def load_insights(self, synthesis_id: str) -> list[dict[str, Any]]:
        """Read-only. Every insight produced by this synthesis run."""
        return self._db.select("insights", INSIGHT_COLUMNS, eq={"synthesis_id": synthesis_id})

    def find_run(self, run_key: str) -> str | None:
        """Id of an existing run with this key, or None."""
        rows = self._db.select("opportunity_runs", "id", eq={"run_key": run_key})
        return str(rows[0]["id"]) if rows else None

    def save(self, payload: Mapping[str, Any]) -> SaveOutcome:
        """Store one validated opportunity run atomically. `exists` = an identical run was already stored."""
        result = self._db.rpc("save_opportunities", {"payload": payload})
        if not isinstance(result, Mapping) or result.get("status") not in ("created", "exists"):
            raise PersistenceError(f"save_opportunities returned an unexpected result: {result!r}")
        outcome = SaveOutcome(
            status=result["status"],
            run_id=str(result["run_id"]),
            opportunities=int(result.get("opportunities", 0)),
        )
        log.info("Opportunity run %s: %s", outcome.status, outcome.run_id)
        return outcome
