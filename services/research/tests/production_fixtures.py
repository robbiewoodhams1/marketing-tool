"""Shared fixtures for the production tests. Nothing here touches the network."""

from __future__ import annotations

import copy
import re

from opportunity_fixtures import JOB_ID, PRODUCT, insight_row, product  # noqa: F401  (re-exported)
from test_persistence import FakeDatabase

from research.persistence import PersistenceError

OPPORTUNITY_ID = "opp-1"
OPPORTUNITY_RUN_ID = "oprun-1"


def opportunity_run_row(**kw):
    # Production always resolves its product from the REAL registry (there is
    # no --product flag: it is inherited from the opportunity's own run), so
    # this must be a real, registered product key - unlike the `testproduct`
    # fixture used directly by the schema/pack/validation-level tests, which
    # build a ProductionInputPack without going through the registry at all.
    row = {
        "id": OPPORTUNITY_RUN_ID, "research_job_id": JOB_ID, "synthesis_id": "syn-1",
        "product_key": "tradeflow", "product_version": "tradeflow-v1",
    }
    row.update(kw)
    return row


def opportunity_row(**kw):
    row = {
        "id": OPPORTUNITY_ID, "opportunity_run_id": OPPORTUNITY_RUN_ID, "research_job_id": JOB_ID,
        "title": "Practical templates in action",
        "audience": "UK sole traders who repeat similar jobs",
        "problem": "Sole traders rebuild the same quote/job/invoice structure every time.",
        "opportunity": "Show how to set up a reusable quote/job/invoice template once and reuse it.",
        "angle": "Lean into the practical, step-by-step format the research shows performs well.",
        "marketing_objective": "activation",
        "suggested_formats": ["tutorial"],
        "cta_direction": "Invite viewers to build a reusable template for a job they repeat often.",
        "product_capability_refs": ["quotes", "reusable_templates"],
        "confidence": 0.6,
        "limitations": "Based on a small sample.",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(kw)
    return row


# --- model output ------------------------------------------------------------------------


def scene(**overrides):
    base = {
        "number": 1, "duration_seconds": 8, "purpose": "Hook: name the repetitive-work problem.",
        "voiceover": "Still rebuilding the same quote every time you do a repeat job?",
        "on_screen_text": "Still rebuilding the same quote?",
        "visual_direction": "Close-up of someone manually retyping a quote on a laptop, looking tired.",
        "product_capability_refs": [], "transition": None,
    }
    base.update(overrides)
    return base


def two_scenes():
    return [
        scene(),
        scene(
            number=2, duration_seconds=10, purpose="Show the reusable template being saved.",
            voiceover="Set the job up once, save it as a template.",
            on_screen_text="Save as template", visual_direction="Screen recording of saving a job as a template.",
            product_capability_refs=["P2"], transition="cut",
        ),
    ]


# P1/P2 deliberately stay valid against BOTH the `testproduct` fixture (used
# directly, bypassing the registry, by the schema/pack/validation tests) and
# the real TradeFlow profile (used via the registry by the service/CLI tests)
# - every product has at least two capabilities, so P1/P2 always resolve.
# What they resolve TO differs (testproduct: quotes/reusable_templates;
# TradeFlow: quotes/jobs) - callers that care about the exact resolved key
# assert against whichever product they actually used.
def product_claims(capability_refs=("P1", "P2"), explanation="Reusable templates and quotes are shown directly on screen being used to skip repeat data entry."):
    return {"capability_refs": list(capability_refs), "explanation": explanation}


def production_output(**overrides):
    scenes = overrides.pop("scenes", None) or two_scenes()
    base = {
        "title": "Stop rebuilding the same quote every time",
        "hook": "Still typing the same job details into a new quote every time?",
        "platform": "tiktok",
        "estimated_duration_seconds": 18,
        "scenes": scenes,
        "cta": "Build your repeat job once. Reuse it every time.",
        "caption": "For tradespeople who do the same job over and over: stop rebuilding it from scratch.",
        "hashtags": ["soletrader"],
        "product_claims": product_claims(),
        "limitations": "Illustrative example; exact on-screen flow may differ from the current UI.",
    }
    base.update(overrides)
    return base


GOOD_OUTPUT = production_output()


# --- fakes -------------------------------------------------------------------------------


class FakeProductionLLM:
    """A ProductionProvider that never touches the network."""

    def __init__(self, out=None, model="production-model", error=None):
        from research.production_schema import ProductionOutput

        self.model = model
        self.error = error
        self._raw = GOOD_OUTPUT if out is None else out
        self._parse = ProductionOutput.from_dict
        self.packs = []

    def generate_production(self, pack):
        self.packs.append(pack)
        if self.error:
            raise self.error
        return self._parse(self._raw)


_HEXKEY_RE = re.compile(r"^[0-9a-f]{64}$")


class RpcFakeDatabase(FakeDatabase):
    """FakeDatabase plus an emulation of `public.save_production`.

    Mirrors the SQL function's contract: one transaction (any failure restores
    the tables), UNIQUE run_key with ON CONFLICT DO NOTHING semantics, and the
    duration-range CHECK. (The real SQL is exercised against Postgres
    separately; see the migration string tests.)
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for t in ("syntheses", "insights", "opportunity_runs", "opportunities",
                  "opportunity_evidence", "production_runs", "productions"):
            self.tables.setdefault(t, [])
        self.rpc_calls = []

    def rpc(self, function, params):
        self.rpc_calls.append((function, params))
        assert function == "save_production"
        snapshot = copy.deepcopy(self.tables)
        try:
            return self._save_production(params["payload"])
        except Exception:
            self.tables = snapshot  # ROLLBACK
            raise

    def _save_production(self, payload):
        r = payload["run"]
        p = payload["production"]
        if not _HEXKEY_RE.match(r["run_key"]):
            raise PersistenceError("check violation: run_key format")
        for col in ("opportunity_id", "research_job_id", "product_key", "product_version",
                    "analysis_type", "model", "prompt_version", "schema_version",
                    "input_manifest", "result"):
            if r.get(col) is None:
                raise PersistenceError(f"not null violation: production_runs.{col}")
        for existing in self.tables["production_runs"]:
            if existing["run_key"] == r["run_key"]:
                return {"status": "exists", "run_id": existing["id"]}
        self._n += 1
        run_id = f"run-{self._n}"
        self.tables["production_runs"].append({"id": run_id, **copy.deepcopy(r)})
        duration = p["estimated_duration_seconds"]
        if not 15 <= duration <= 65:
            raise PersistenceError("check violation: estimated_duration_seconds range")
        self._n += 1
        prod_id = f"prod-{self._n}"
        self.tables["productions"].append({
            "id": prod_id, "production_run_id": run_id, "opportunity_id": r["opportunity_id"],
            **copy.deepcopy(p),
        })
        return {"status": "created", "run_id": run_id, "production_id": prod_id}


def seeded_db(insights=None, evidence_insight_ids=None):
    """A database holding one completed job, one synthesis, its insights, one
    opportunity run and one opportunity citing some of them - ready for production."""
    db = RpcFakeDatabase()
    db.tables["research_jobs"].append({
        "id": JOB_ID, "query": "UK sole trader accounting", "audience": "UK sole traders",
        "objective": "find hooks", "status": "completed",
        "started_at": "2026-01-01T00:00:00+00:00", "completed_at": "2026-01-01T01:00:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00"})
    db.tables["syntheses"].append({
        "id": "syn-1", "research_job_id": JOB_ID, "analysis_type": "insight_synthesis",
        "model": "synth-model", "prompt_version": "synthesis-v3", "schema_version": "synthesis-v1",
        "run_key": "b" * 64, "result": {}, "created_at": "2026-01-01T01:00:00+00:00"})
    rows = insights if insights is not None else [insight_row(1), insight_row(2)]
    db.tables["insights"] += rows
    db.tables["opportunity_runs"].append(opportunity_run_row())
    db.tables["opportunities"].append(opportunity_row())
    cited = evidence_insight_ids if evidence_insight_ids is not None else [r["id"] for r in rows]
    for insight_id in cited:
        db.tables["opportunity_evidence"].append(
            {"opportunity_id": OPPORTUNITY_ID, "insight_id": insight_id, "note": "supports the opportunity"}
        )
    return db
