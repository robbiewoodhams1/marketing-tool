"""Shared fixtures for the synthesis tests. Nothing here touches the network."""

import copy
import re

from test_persistence import FakeDatabase

from research.classification import ClassificationResult, FieldResult
from research.persistence import PersistenceError

JOB_ID = "job-1"
CLASSIFICATION_SCHEMA = "classification-v1"


def content_row(n, **kw):
    row = {
        "id": f"content-{n}",
        "external_id": f"vid{n:08d}",
        "url": f"https://www.youtube.com/watch?v=vid{n:08d}",
        "title": f"Title {n}",
        "creator": f"Creator {n}",
        "description": None,
        "published_at": f"2026-01-0{n}T00:00:00+00:00",
        "views": 1000 * n,
        "likes": 10 * n,
        "comments_count": n,
        "shares": None,
        "saves": None,
        "transcript": None,
    }
    row.update(kw)
    return row


def standard_content():
    """Four videos: two by one creator, one with NULL likes and no transcript."""
    return [
        content_row(1, title="MTD for sole traders: 5 things to know", creator="AccountsCo",
                    views=1200, likes=60, comments_count=10,
                    transcript="Stop what you are doing. 860,000 sole traders are now required "
                               "to use Making Tax Digital. Here is what changes."),
        content_row(2, title="Making Tax Digital explained in 5 minutes", creator="AccountsCo",
                    views=5400, likes=200, comments_count=25,
                    transcript="If you are a sole trader you need to know these three "
                               "requirements. Requirement one is digital records."),
        content_row(3, title="Best accounting software for freelancers", creator="FreelanceTV",
                    views=20000, likes=400, comments_count=50,
                    description="Ranked by price and features.",
                    transcript="Here are the top five accounting apps ranked by price."),
        content_row(4, title="Tax return mistakes", creator="TaxTips", views=300, likes=None,
                    comments_count=None, description="Avoid common errors"),
    ]


def comment_row(n, content_n, text, likes=0, **kw):
    row = {"id": f"comment-{n}", "content_id": f"content-{content_n}",
           "external_id": f"c{n}", "text": text, "likes": likes, "type": "top_level"}
    row.update(kw)
    return row


def standard_comments():
    return [
        comment_row(1, 1, "I am terrified of the penalties", 12),
        comment_row(2, 1, "Is MTD compulsory for me?", 3),
        comment_row(3, 2, "Finally a clear explanation of MTD", 8),
        comment_row(4, 3, "Which software is cheapest?", 5),
    ]


def result(**overrides):
    fields = {
        "topic": FieldResult("tax", 0.9, "Making Tax Digital"),
        "audience": FieldResult("sole traders", 0.8, "sole traders"),
        "pain_point": FieldResult("penalties", 0.7, "required"),
        "hook": FieldResult("Stop", 0.8, "Stop what you are doing"),
        "hook_type": FieldResult("fear", 0.88, "now required"),
        "format": FieldResult("tutorial", 0.6, "explained"),
        "emotion": FieldResult("urgency", 0.5, "now"),
        "cta": FieldResult(None),
    }
    fields.update(overrides)
    return ClassificationResult(**fields).to_dict()


def interpretation_row(n, content_n, *, created="2026-09-26T21:54:03+00:00", model="haiku",
                       prompt="classification-v2", res=None, **kw):
    row = {"id": f"interp-{n}", "content_id": f"content-{content_n}",
           "analysis_type": "classification", "model": model, "prompt_version": prompt,
           "schema_version": CLASSIFICATION_SCHEMA,
           "result": res if res is not None else result(), "created_at": created}
    row.update(kw)
    return row


def standard_interpretations():
    """Content 1-3 classified, content 4 not: coverage 0.75."""
    return [interpretation_row(1, 1), interpretation_row(2, 2), interpretation_row(3, 3)]


JOB = {"query": "UK sole trader accounting", "audience": "UK sole traders", "objective": "find hooks"}


def build_pack(content=None, comments=None, interpretations=None, **kw):
    from research.evidence_pack import build_evidence_pack

    return build_evidence_pack(
        job=JOB,
        content_rows=standard_content() if content is None else content,
        comment_rows=standard_comments() if comments is None else comments,
        interpretation_rows=standard_interpretations() if interpretations is None else interpretations,
        **kw,
    )


# --- model outputs --------------------------------------------------------------


def ev(ref, field, quote):
    return {"ref": ref, "field": field, "quote": quote}


def insight(**overrides):
    base = {
        "title": "Making Tax Digital is framed as a requirement",
        "pattern_type": "hook_pattern",
        "statement": "Among the analysed videos, two videos frame Making Tax Digital as a "
                     "requirement for sole traders.",
        "observations": [
            {"text": "C1 opens by saying sole traders are now required to use Making Tax Digital.",
             "evidence": [ev("C1", "transcript",
                             "860,000 sole traders are now required to use Making Tax Digital")]},
            {"text": "C2 is titled as an explanation of Making Tax Digital.",
             "evidence": [ev("C2", "title", "Making Tax Digital explained in 5 minutes")]},
        ],
        "explanation": "Both videos name Making Tax Digital in their opening material.",
        "confidence": 0.6,
        "confidence_rationale": "Two videos in a small sample.",
        "counter_evidence": [],
        "limitations": "Small sample of 4 videos from 3 creators; excerpts show only openings.",
    }
    base.update(overrides)
    return base


def output(*insights, reason=None):
    return {"insights": list(insights), "no_insights_reason": reason}


GOOD_OUTPUT = output(insight())
ZERO_OUTPUT = output(reason="The evidence does not support any recurring pattern.")


# --- fakes ------------------------------------------------------------------------


class FakeSynthesisLLM:
    """A SynthesisProvider that never touches the network."""

    def __init__(self, out=None, model="synth-model", error=None):
        from research.synthesis import SynthesisOutput

        self.model = model
        self.error = error
        self._raw = GOOD_OUTPUT if out is None else out
        self._parse = SynthesisOutput.from_dict
        self.packs = []

    def synthesize_research(self, pack):
        self.packs.append(pack)
        if self.error:
            raise self.error
        return self._parse(self._raw)


_UUIDLIKE = re.compile(r"^[0-9a-f]{64}$")


class RpcFakeDatabase(FakeDatabase):
    """FakeDatabase plus an emulation of `public.save_synthesis`.

    Mirrors the SQL function's contract: one transaction (any failure restores
    the tables), UNIQUE run_key with ON CONFLICT DO NOTHING semantics, the
    exactly-one-target / role / observation_index CHECKs, real FK existence for
    content/comment/interpretation, the composite job consistency, and the
    cross-job evidence backstop. (The real SQL is exercised against Postgres in
    the migration verification; see the completion report.)
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for t in ("syntheses", "insights", "insight_evidence"):
            self.tables.setdefault(t, [])
        self.rpc_calls = []
        self.fail_rpc_after_insights = None  # raise after N insights are staged

    def rpc(self, function, params):
        self.rpc_calls.append((function, params))
        assert function == "save_synthesis"
        snapshot = copy.deepcopy(self.tables)
        try:
            return self._save_synthesis(params["payload"])
        except Exception:
            self.tables = snapshot  # ROLLBACK
            raise

    def _find(self, table, id_):
        return next((r for r in self.tables[table] if r["id"] == id_), None)

    def _save_synthesis(self, payload):
        s = payload["synthesis"]
        if not _UUIDLIKE.match(s["run_key"]):
            raise PersistenceError("check violation: run_key format")
        for col in ("research_job_id", "analysis_type", "model", "prompt_version",
                    "schema_version", "input_manifest", "result"):
            if s.get(col) is None:
                raise PersistenceError(f"not null violation: syntheses.{col}")
        for existing in self.tables["syntheses"]:
            if existing["run_key"] == s["run_key"]:
                return {"status": "exists", "synthesis_id": existing["id"]}
        self._n += 1
        syn_id = f"syn-{self._n}"
        self.tables["syntheses"].append({"id": syn_id, **copy.deepcopy(s)})
        count = 0
        for ins in payload["insights"]:
            if self.fail_rpc_after_insights is not None and count >= self.fail_rpc_after_insights:
                raise PersistenceError("simulated failure mid-write")
            if not 0 <= ins["model_confidence"] <= 1:
                raise PersistenceError("check violation: model_confidence")
            self._n += 1
            ins_id = f"ins-{self._n}"
            self.tables["insights"].append({
                "id": ins_id, "research_job_id": s["research_job_id"], "synthesis_id": syn_id,
                **{k: copy.deepcopy(v) for k, v in ins.items() if k != "evidence"},
            })
            count += 1
            for e in ins["evidence"]:
                targets = [e.get("content_id"), e.get("comment_id"), e.get("interpretation_id")]
                if sum(t is not None for t in targets) != 1:
                    raise PersistenceError("check violation: exactly one evidence target")
                if (e["role"] == "supports") != (e.get("observation_index") is not None):
                    raise PersistenceError("check violation: role/observation_index")
                if e["role"] not in ("supports", "counter") or not e["quote"].strip():
                    raise PersistenceError("check violation: role/quote")
                if e.get("content_id"):
                    row = self._find("content", e["content_id"])
                    content_id = e["content_id"]
                elif e.get("comment_id"):
                    row = self._find("comments", e["comment_id"])
                    content_id = row["content_id"] if row else None
                else:
                    row = self._find("interpretations", e["interpretation_id"])
                    content_id = row["content_id"] if row else None
                if row is None:
                    raise PersistenceError("foreign key violation: evidence target")
                owner = self._find("content", content_id)
                if owner["research_job_id"] != s["research_job_id"]:
                    raise PersistenceError("cited evidence belongs to a different research job")
                self.tables["insight_evidence"].append(
                    {"id": f"ev-{self._n}-{len(self.tables['insight_evidence'])}",
                     "insight_id": ins_id, **copy.deepcopy(e)})
        return {"status": "created", "synthesis_id": syn_id, "insights": count}


def seeded_db(job_status="completed", content=None, comments=None, interpretations=None):
    """A database holding one job with the standard evidence, ready to synthesise."""
    db = RpcFakeDatabase()
    db.tables["research_jobs"].append({
        "id": JOB_ID, "query": JOB["query"], "audience": JOB["audience"],
        "objective": JOB["objective"], "status": job_status,
        "started_at": "2026-01-01T00:00:00+00:00", "completed_at": "2026-01-01T01:00:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00"})
    for row in standard_content() if content is None else content:
        db.tables["content"].append({"research_job_id": JOB_ID, "platform": "youtube", **row})
    db.tables["comments"] += standard_comments() if comments is None else comments
    db.tables["interpretations"] += (
        standard_interpretations() if interpretations is None else interpretations)
    return db
