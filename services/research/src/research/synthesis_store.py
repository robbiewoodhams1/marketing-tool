"""Load a job's evidence and persist a validated synthesis.

Writes go through the `Database` protocol so tests never need Supabase. The
synthesis itself is stored by ONE call to the Postgres function
`save_synthesis` (a single transaction): a run row, its insights and their
evidence rows all persist, or none do. The function stores an already-validated
payload; all validation happens in Python (`research.synthesis_validation`).

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

from research.evidence_pack import EvidencePack
from research.logging import get_logger
from research.persistence import (
    LOOKUP_BATCH_SIZE,
    PLATFORM_YOUTUBE,
    Database,
    PersistenceError,
    ResearchJob,
    ResearchRepository,
    _chunks,
)
from research.synthesis import (
    SYNTHESIS_ANALYSIS_TYPE,
    SYNTHESIS_SCHEMA_VERSION,
    SynthesisOutput,
)
from research.synthesis_prompt import PROMPT_VERSION
from research.synthesis_validation import ValidatedSynthesis

log = get_logger("research.synthesis_store")

CONTENT_COLUMNS = (
    "id,external_id,url,title,creator,description,published_at,views,likes,"
    "comments_count,shares,saves,transcript"
)
COMMENT_COLUMNS = "id,content_id,external_id,text,likes,type"
INTERPRETATION_COLUMNS = "id,content_id,analysis_type,model,prompt_version,schema_version,result,created_at"


@dataclass(frozen=True)
class RawEvidence:
    """Everything loaded for a job. Rows are plain dicts, exactly as stored."""

    job: ResearchJob
    content: list[dict[str, Any]]
    comments: list[dict[str, Any]]
    interpretations: list[dict[str, Any]]


@dataclass(frozen=True)
class SaveOutcome:
    status: str  # "created" | "exists"
    synthesis_id: str
    insights: int


def compute_run_key(
    *,
    research_job_id: str,
    model: str,
    pack: EvidencePack,
    analysis_type: str = SYNTHESIS_ANALYSIS_TYPE,
    prompt_version: str | None = None,
    schema_version: str | None = None,
) -> str:
    """SHA-256 of the canonical run configuration.

    Canonical = JSON with sorted keys, no insignificant whitespace, UTF-8, and
    every id list sorted. Anything that changes what the model saw or how it
    was asked changes the key; nothing else does.
    """
    identity = {
        "analysis_type": analysis_type,
        "research_job_id": research_job_id,
        "model": model,
        "prompt_version": prompt_version or PROMPT_VERSION,
        "schema_version": schema_version or SYNTHESIS_SCHEMA_VERSION,
        "pack_version": pack.manifest["pack_version"],
        "selected_content_ids": sorted(pack.content_ids),
        "selected_interpretation_ids": sorted(pack.interpretation_ids),
        "selected_comment_ids": sorted(pack.comment_ids),
        "bounds": pack.bounds,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_save_payload(
    *,
    research_job_id: str,
    model: str,
    run_key: str,
    pack: EvidencePack,
    output: SynthesisOutput,
    validated: ValidatedSynthesis,
) -> dict[str, Any]:
    """The exact payload handed to `save_synthesis`."""
    return {
        "synthesis": {
            "research_job_id": research_job_id,
            "analysis_type": SYNTHESIS_ANALYSIS_TYPE,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SYNTHESIS_SCHEMA_VERSION,
            "run_key": run_key,
            "input_manifest": pack.manifest,
            "result": output.to_dict(),
        },
        "insights": [
            {
                "title": v.insight.title,
                "statement": v.insight.statement,
                "pattern_type": v.insight.pattern_type.value,
                "model_confidence": v.insight.confidence,
                "confidence_rationale": v.insight.confidence_rationale,
                "explanation": v.insight.explanation,
                "limitations": v.insight.limitations,
                "observations": [{"text": o.text} for o in v.insight.observations],
                "support": v.support,
                "evidence": [
                    {
                        "role": e.role,
                        "observation_index": e.observation_index,
                        "content_id": e.content_id,
                        "comment_id": e.comment_id,
                        "interpretation_id": e.interpretation_id,
                        "field": e.field,
                        "quote": e.quote,
                    }
                    for e in v.evidence
                ],
            }
            for v in validated.insights
        ],
    }


class SynthesisRepository:
    def __init__(self, db: Database):
        self._db = db

    def get_job(self, job_id: str) -> ResearchJob | None:
        return ResearchRepository(self._db).get_job(job_id)

    def load_evidence(self, job: ResearchJob) -> RawEvidence:
        """Read-only. Raw content, its comments, and its classification interpretations."""
        content = self._db.select(
            "content", CONTENT_COLUMNS, eq={"research_job_id": job.id, "platform": PLATFORM_YOUTUBE}
        )
        ids = [c["id"] for c in content]
        comments: list[dict[str, Any]] = []
        interpretations: list[dict[str, Any]] = []
        for chunk in _chunks(ids, LOOKUP_BATCH_SIZE):
            comments += self._db.select("comments", COMMENT_COLUMNS, in_=("content_id", list(chunk)))
            interpretations += self._db.select(
                "interpretations",
                INTERPRETATION_COLUMNS,
                eq={"analysis_type": "classification"},
                in_=("content_id", list(chunk)),
            )
        return RawEvidence(job=job, content=content, comments=comments, interpretations=interpretations)

    def find_run(self, run_key: str) -> str | None:
        """Id of an existing run with this key, or None."""
        rows = self._db.select("syntheses", "id", eq={"run_key": run_key})
        return str(rows[0]["id"]) if rows else None

    def save(self, payload: Mapping[str, Any]) -> SaveOutcome:
        """Store one validated synthesis atomically. `exists` = an identical run was already stored."""
        result = self._db.rpc("save_synthesis", {"payload": payload})
        if not isinstance(result, Mapping) or result.get("status") not in ("created", "exists"):
            raise PersistenceError(f"save_synthesis returned an unexpected result: {result!r}")
        outcome = SaveOutcome(
            status=result["status"],
            synthesis_id=str(result["synthesis_id"]),
            insights=int(result.get("insights", 0)),
        )
        log.info("Synthesis %s: %s", outcome.status, outcome.synthesis_id)
        return outcome

