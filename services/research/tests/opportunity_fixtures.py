"""Shared fixtures for the opportunity-creation tests. Nothing here touches the network."""

from __future__ import annotations

import copy
import re

from test_persistence import FakeDatabase

from research.persistence import PersistenceError
from research.product_context import ProductCapability, ProductProfile

JOB_ID = "job-1"
SYNTHESIS_ID = "syn-1"

JOB = {"query": "UK sole trader accounting", "audience": "UK sole traders", "objective": "find hooks"}


# --- a small test product profile, independent of the real TradeFlow profile ---------------


def product(**overrides) -> ProductProfile:
    fields = dict(
        key="testproduct",
        version="testproduct-v1",
        name="TestProduct",
        description="A product for tests that helps with quotes, jobs and invoices.",
        audience=("UK sole traders",),
        capabilities=(
            ProductCapability(
                key="quotes", name="Quotes", description="Create and send quotes.",
                problem_solved="Producing a quote for a customer.",
            ),
            ProductCapability(
                key="reusable_templates", name="Reusable templates",
                description="Save a quote, job or invoice as a reusable template.",
                problem_solved="Rebuilding the same structure from scratch for recurring work.",
            ),
        ),
        differentiators=("Combines templates and quotes in one place.",),
        positioning="Reduces repetitive admin work.",
        limitations=(),
        out_of_scope=("bookkeeping", "tax return", "accounting", "expense tracking"),
        pricing=None,
        marketing_objectives=(),
    )
    fields.update(overrides)
    return ProductProfile(**fields)


PRODUCT = product()


# --- insight rows (as stored in `insights`, i.e. already synthesis-validated) -------------


def insight_row(n, **kw):
    row = {
        "id": f"insight-{n}",
        "research_job_id": JOB_ID,
        "synthesis_id": SYNTHESIS_ID,
        "title": f"Insight {n} title",
        "statement": f"Among the analysed videos, pattern {n} recurs.",
        "pattern_type": "recurring_theme",
        "model_confidence": 0.6,
        "confidence_rationale": "Two videos in a small sample.",
        "explanation": f"Explanation for insight {n}.",
        "limitations": "Small sample.",
        "observations": [{"text": f"Observation {n}."}],
        "support": {"content_count": 2, "computed_by": "code"},
    }
    row.update(kw)
    return row


def standard_insights():
    return [
        insight_row(
            1, title="Practical how-to videos attract grateful comments",
            statement="Among the analysed videos, step-by-step procedural content draws grateful comments.",
        ),
        insight_row(
            2, title="Bookkeeping videos draw unresolved questions",
            statement="Among the analysed videos, bookkeeping content generates unanswered questions.",
        ),
    ]


# --- model outputs -----------------------------------------------------------------------


def research_evidence(ref="N1", note="This insight shows the audience values practical, step-by-step content."):
    return {"ref": ref, "note": note}


def product_fit(capability_refs=("P1",), explanation="Quotes let a sole trader act on this immediately."):
    return {"capability_refs": list(capability_refs), "explanation": explanation}


def opportunity(**overrides):
    base = {
        "title": "Practical quoting guide for repeat jobs",
        "audience": "UK sole traders",
        "problem": "Sole traders repeatedly rebuild similar quotes for recurring work.",
        "opportunity": "A practical guide showing how to set up a reusable quote once and reuse it for repeat jobs.",
        "angle": "Lean into the 'practical how-to' format the research shows performs well.",
        "marketing_objective": "acquisition",
        "suggested_formats": ["tutorial"],
        "cta_direction": "Invite viewers to try building a reusable quote template for their own repeat work.",
        "research_evidence": [research_evidence()],
        "product_fit": product_fit(),
        "confidence": 0.6,
        "limitations": "Based on a single insight from a small sample.",
    }
    base.update(overrides)
    return base


def output(*opportunities, reason=None):
    return {"opportunities": list(opportunities), "no_opportunities_reason": reason}


GOOD_OUTPUT = output(opportunity())
ZERO_OUTPUT = output(reason="No research insight meaningfully connects to what the product does.")


# --- fakes -------------------------------------------------------------------------------


class FakeOpportunityLLM:
    """An OpportunityProvider that never touches the network."""

    def __init__(self, out=None, model="opportunity-model", error=None):
        from research.opportunity import OpportunityCreationOutput

        self.model = model
        self.error = error
        self._raw = GOOD_OUTPUT if out is None else out
        self._parse = OpportunityCreationOutput.from_dict
        self.packs = []

    def generate_opportunities(self, pack):
        self.packs.append(pack)
        if self.error:
            raise self.error
        return self._parse(self._raw)


_HEXKEY_RE = re.compile(r"^[0-9a-f]{64}$")


class RpcFakeDatabase(FakeDatabase):
    """FakeDatabase plus an emulation of `public.save_opportunities`.

    Mirrors the SQL function's contract: one transaction (any failure restores
    the tables), UNIQUE run_key with ON CONFLICT DO NOTHING semantics, real FK
    existence for cited insights, and the cross-synthesis/job evidence
    backstop. (The real SQL is exercised against Postgres separately; see the
    migration string tests.)
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for t in ("syntheses", "insights", "opportunity_runs", "opportunities", "opportunity_evidence"):
            self.tables.setdefault(t, [])
        self.rpc_calls = []
        self.fail_rpc_after_opportunities = None

    def rpc(self, function, params):
        self.rpc_calls.append((function, params))
        assert function == "save_opportunities"
        snapshot = copy.deepcopy(self.tables)
        try:
            return self._save_opportunities(params["payload"])
        except Exception:
            self.tables = snapshot  # ROLLBACK
            raise

    def _find(self, table, id_):
        return next((r for r in self.tables[table] if r["id"] == id_), None)

    def _save_opportunities(self, payload):
        r = payload["run"]
        if not _HEXKEY_RE.match(r["run_key"]):
            raise PersistenceError("check violation: run_key format")
        for col in ("research_job_id", "synthesis_id", "product_key", "product_version",
                    "analysis_type", "model", "prompt_version", "schema_version",
                    "input_manifest", "result"):
            if r.get(col) is None:
                raise PersistenceError(f"not null violation: opportunity_runs.{col}")
        for existing in self.tables["opportunity_runs"]:
            if existing["run_key"] == r["run_key"]:
                return {"status": "exists", "run_id": existing["id"]}
        self._n += 1
        run_id = f"run-{self._n}"
        self.tables["opportunity_runs"].append({"id": run_id, **copy.deepcopy(r)})
        count = 0
        for opp in payload["opportunities"]:
            if self.fail_rpc_after_opportunities is not None and count >= self.fail_rpc_after_opportunities:
                raise PersistenceError("simulated failure mid-write")
            if not 0 <= opp["confidence"] <= 1:
                raise PersistenceError("check violation: confidence")
            self._n += 1
            opp_id = f"opp-{self._n}"
            self.tables["opportunities"].append({
                "id": opp_id, "opportunity_run_id": run_id, "research_job_id": r["research_job_id"],
                **{k: copy.deepcopy(v) for k, v in opp.items() if k != "evidence"},
            })
            count += 1
            for e in opp["evidence"]:
                insight = self._find("insights", e["insight_id"])
                if insight is None:
                    raise PersistenceError("foreign key violation: insight_id")
                if not e["note"].strip():
                    raise PersistenceError("check violation: note not blank")
                self.tables["opportunity_evidence"].append({
                    "id": f"ev-{self._n}-{len(self.tables['opportunity_evidence'])}",
                    "opportunity_id": opp_id, "insight_id": e["insight_id"], "note": e["note"],
                })
                if (insight["research_job_id"] != r["research_job_id"]
                        or insight["synthesis_id"] != r["synthesis_id"]):
                    raise PersistenceError(
                        "cited insight belongs to a different synthesis or research job")
        return {"status": "created", "run_id": run_id, "opportunities": count}


def seeded_db(job_status="completed", insights=None):
    """A database holding one completed job, one synthesis and its insights,
    ready for opportunity creation."""
    db = RpcFakeDatabase()
    db.tables["research_jobs"].append({
        "id": JOB_ID, "query": JOB["query"], "audience": JOB["audience"],
        "objective": JOB["objective"], "status": job_status,
        "started_at": "2026-01-01T00:00:00+00:00", "completed_at": "2026-01-01T01:00:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00"})
    db.tables["syntheses"].append({
        "id": SYNTHESIS_ID, "research_job_id": JOB_ID, "analysis_type": "insight_synthesis",
        "model": "synth-model", "prompt_version": "synthesis-v3", "schema_version": "synthesis-v1",
        "run_key": "b" * 64, "result": {}, "created_at": "2026-01-01T01:00:00+00:00"})
    db.tables["insights"] += standard_insights() if insights is None else insights
    return db
