"""Load a persisted Opportunity (and its research provenance) and persist a
validated Production run.

Mirrors `research.opportunity_store`: writes go through the `Database`
protocol so tests never need Supabase. The run itself is stored by ONE call to
the Postgres function `save_production` (a single transaction): a run row and
its one production row persist together, or neither does. The function stores
an already-validated payload; all validation happens in Python
(`research.production_validation`).

`run_key` is UNIQUE in the database, so the database - not this code - is the
final guarantee that an identical run is never stored twice, including under
concurrent execution. `find_run` is only an optimisation that avoids paying
for an LLM call whose result would be discarded.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from research.logging import get_logger
from research.persistence import Database, PersistenceError, _chunks
from research.production_pack import ProductionInputPack
from research.production_prompt import PROMPT_VERSION
from research.production_schema import (
    PRODUCTION_ANALYSIS_TYPE,
    PRODUCTION_SCHEMA_VERSION,
    ProductionOutput,
)
from research.production_validation import ValidatedProduction

log = get_logger("research.production_store")

OPPORTUNITY_COLUMNS = (
    "id,opportunity_run_id,research_job_id,title,audience,problem,opportunity,angle,"
    "marketing_objective,suggested_formats,cta_direction,product_capability_refs,"
    "confidence,limitations,created_at"
)
OPPORTUNITY_RUN_COLUMNS = "id,research_job_id,synthesis_id,product_key,product_version"
OPPORTUNITY_EVIDENCE_COLUMNS = "opportunity_id,insight_id,note"
INSIGHT_COLUMNS = "id,title,statement,limitations"


@dataclass(frozen=True)
class OpportunityRecord:
    """An existing `opportunities` row plus its run's product identity, as the
    production engine needs it."""

    id: str
    research_job_id: str
    product_key: str
    product_version: str
    row: Mapping[str, Any]  # the full row, exactly as stored


@dataclass(frozen=True)
class SaveOutcome:
    status: str  # "created" | "exists"
    run_id: str
    production_id: str | None


def compute_run_key(
    *,
    opportunity_id: str,
    model: str,
    pack: ProductionInputPack,
    analysis_type: str = PRODUCTION_ANALYSIS_TYPE,
    prompt_version: str | None = None,
    schema_version: str | None = None,
) -> str:
    """SHA-256 of the canonical run configuration.

    Canonical = JSON with sorted keys, no insignificant whitespace, UTF-8.
    Anything that changes what the model saw, which product it saw, or how it
    was asked changes the key; nothing else does. The opportunity itself is
    immutable, so `opportunity_id` alone stands in for its content.
    """
    identity = {
        "analysis_type": analysis_type,
        "opportunity_id": opportunity_id,
        "model": model,
        "prompt_version": prompt_version or PROMPT_VERSION,
        "schema_version": schema_version or PRODUCTION_SCHEMA_VERSION,
        "pack_version": pack.manifest["pack_version"],
        "product_key": pack.product_key,
        "product_version": pack.product_version,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_save_payload(
    *,
    opportunity: OpportunityRecord,
    model: str,
    run_key: str,
    pack: ProductionInputPack,
    output: ProductionOutput,
    validated: ValidatedProduction,
) -> dict[str, Any]:
    """The exact payload handed to `save_production`.

    `target_audience` and `objective` are copied here from the opportunity's
    own `audience` and `marketing_objective` - never asked of the model (see
    `research.production_prompt`). `script` is assembled here, deterministically,
    from the hook, each scene's voiceover, and the cta - never asked of the
    model either, so there is exactly one place the words came from.
    """
    script_lines = [output.hook] + [s.voiceover for s in output.scenes] + [output.cta]
    script = "\n\n".join(line for line in script_lines if line)
    return {
        "run": {
            "opportunity_id": opportunity.id,
            "research_job_id": opportunity.research_job_id,
            "product_key": pack.product_key,
            "product_version": pack.product_version,
            "analysis_type": PRODUCTION_ANALYSIS_TYPE,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "schema_version": PRODUCTION_SCHEMA_VERSION,
            "run_key": run_key,
            "input_manifest": pack.manifest,
            "result": output.to_dict(),
        },
        "production": {
            "title": output.title,
            "hook": output.hook,
            "target_audience": opportunity.row.get("audience"),
            "objective": opportunity.row.get("marketing_objective"),
            "platform": output.platform.value,
            "estimated_duration_seconds": output.estimated_duration_seconds,
            "scenes": [
                {
                    "number": vs.scene.number,
                    "duration_seconds": vs.scene.duration_seconds,
                    "purpose": vs.scene.purpose,
                    "voiceover": vs.scene.voiceover,
                    "on_screen_text": vs.scene.on_screen_text,
                    "visual_direction": vs.scene.visual_direction,
                    "product_capability_refs": list(vs.capability_keys),
                    "transition": vs.scene.transition,
                }
                for vs in validated.scenes
            ],
            "script": script,
            "cta": output.cta,
            "caption": output.caption,
            "hashtags": list(output.hashtags),
            "product_capability_refs": list(validated.capability_keys),
            "limitations": output.limitations,
        },
    }


class ProductionRepository:
    def __init__(self, db: Database):
        self._db = db

    def get_opportunity(self, opportunity_id: str) -> OpportunityRecord | None:
        rows = self._db.select("opportunities", OPPORTUNITY_COLUMNS, eq={"id": opportunity_id})
        if not rows:
            return None
        row = rows[0]
        run_rows = self._db.select(
            "opportunity_runs", OPPORTUNITY_RUN_COLUMNS, eq={"id": row["opportunity_run_id"]}
        )
        if not run_rows:
            raise PersistenceError(
                f"opportunity {opportunity_id} references opportunity_run "
                f"{row['opportunity_run_id']}, which does not exist"
            )
        run = run_rows[0]
        return OpportunityRecord(
            id=str(row["id"]),
            research_job_id=str(row["research_job_id"]),
            product_key=run["product_key"],
            product_version=run["product_version"],
            row=row,
        )

    def load_research_context(self, opportunity_id: str) -> list[dict[str, Any]]:
        """Read-only. The insights this opportunity cites, as background context."""
        evidence = self._db.select(
            "opportunity_evidence", OPPORTUNITY_EVIDENCE_COLUMNS, eq={"opportunity_id": opportunity_id}
        )
        insight_ids = sorted({e["insight_id"] for e in evidence})
        insights: list[dict[str, Any]] = []
        for chunk in _chunks(insight_ids, 100):
            insights += self._db.select("insights", INSIGHT_COLUMNS, in_=("id", list(chunk)))
        return insights

    def find_run(self, run_key: str) -> str | None:
        """Id of an existing run with this key, or None."""
        rows = self._db.select("production_runs", "id", eq={"run_key": run_key})
        return str(rows[0]["id"]) if rows else None

    def save(self, payload: Mapping[str, Any]) -> SaveOutcome:
        """Store one validated production atomically. `exists` = an identical run was already stored."""
        result = self._db.rpc("save_production", {"payload": payload})
        if not isinstance(result, Mapping) or result.get("status") not in ("created", "exists"):
            raise PersistenceError(f"save_production returned an unexpected result: {result!r}")
        outcome = SaveOutcome(
            status=result["status"],
            run_id=str(result["run_id"]),
            production_id=str(result["production_id"]) if result.get("production_id") else None,
        )
        log.info("Production run %s: %s", outcome.status, outcome.run_id)
        return outcome
