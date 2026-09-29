import copy
import re
from pathlib import Path

import httpx
import pytest
from opportunity_fixtures import (
    GOOD_OUTPUT,
    JOB_ID,
    PRODUCT,
    SYNTHESIS_ID,
    ZERO_OUTPUT,
    FakeOpportunityLLM,
    RpcFakeDatabase,
    insight_row,
    opportunity,
    output,
    seeded_db,
    standard_insights,
)

from research import opportunities
from research.llm import LLMNetworkError
from research.opportunity import OpportunityValidationError
from research.opportunity_pack import build_opportunity_pack
from research.opportunity_prompt import PROMPT_VERSION
from research.opportunity_store import OpportunityRepository, compute_run_key
from research.opportunities import OpportunitySourceError, run_opportunity_creation
from research.persistence import PersistenceError, SupabaseDatabase

SECRET = "sr-secret-not-real"
MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def run(db, llm=None, product=None, **kw):
    lines = []
    llm = llm or FakeOpportunityLLM()
    outcome = run_opportunity_creation(
        SYNTHESIS_ID, product or PRODUCT, repository=OpportunityRepository(db), llm=llm,
        progress=lines.append, **kw
    )
    return outcome, llm, lines


def snapshot(db):
    return copy.deepcopy(db.tables)


# --- run identity ------------------------------------------------------------------------------


def pack(rows=None):
    return build_opportunity_pack(
        job={"query": "q", "audience": "a", "objective": "o"},
        synthesis={"id": SYNTHESIS_ID, "research_job_id": JOB_ID},
        insight_rows=standard_insights() if rows is None else rows,
        product=PRODUCT,
    )


PACK = pack()


def key(p=PACK, **kw):
    return compute_run_key(**{
        "synthesis_id": SYNTHESIS_ID, "research_job_id": JOB_ID, "model": "m", "pack": p, **kw,
    })


def test_run_key_is_a_sha256_hex_digest_and_stable():
    k = key()
    assert re.fullmatch(r"[0-9a-f]{64}", k) and k == key() == key(pack())


def test_run_key_is_independent_of_insight_row_order():
    forward = pack(list(reversed(standard_insights())))
    backward = pack(standard_insights())
    assert key(forward) == key(backward)


@pytest.mark.parametrize("change", [
    {"model": "other-model"},
    {"prompt_version": PROMPT_VERSION + "-next"},
    {"schema_version": "opportunity-v2"},
    {"analysis_type": "other_analysis"},
    {"synthesis_id": "syn-2"},
    {"research_job_id": "job-2"},
])
def test_changing_any_configuration_input_changes_the_run_key(change):
    assert key(**change) != key()


def test_changing_the_selected_insights_changes_the_run_key():
    assert key(pack([insight_row(1)])) != key()
    assert key(pack([insight_row(1), insight_row(2), insight_row(3)])) != key()


def test_changing_the_product_profile_changes_the_run_key():
    from opportunity_fixtures import product

    other_pack = build_opportunity_pack(
        job={"query": "q", "audience": "a", "objective": "o"},
        synthesis={"id": SYNTHESIS_ID, "research_job_id": JOB_ID},
        insight_rows=standard_insights(), product=product(version="testproduct-v2"),
    )
    assert key(other_pack) != key()


# --- the happy path ------------------------------------------------------------------------------


def test_a_complete_opportunity_run_is_persisted_with_provenance_and_evidence():
    db = seeded_db()
    outcome, llm, lines = run(db)
    assert outcome.status == "created" and outcome.opportunities == 1

    [r] = db.tables["opportunity_runs"]
    assert (r["research_job_id"], r["synthesis_id"], r["model"]) == (JOB_ID, SYNTHESIS_ID, "opportunity-model")
    assert r["product_key"] == "testproduct" and r["product_version"] == "testproduct-v1"
    assert r["prompt_version"] == PROMPT_VERSION and r["schema_version"] == "opportunity-v1"
    assert re.fullmatch(r"[0-9a-f]{64}", r["run_key"]) and r["run_key"] == outcome.run_key
    assert r["input_manifest"] == llm.packs[0].manifest
    assert r["result"] == GOOD_OUTPUT

    [opp] = db.tables["opportunities"]
    assert (opp["opportunity_run_id"], opp["research_job_id"]) == (r["id"], JOB_ID)
    assert opp["marketing_objective"] == "acquisition" and opp["suggested_formats"] == ["tutorial"]
    assert opp["product_capability_refs"] == ["quotes"]
    assert opp["confidence"] == 0.6

    [ev] = db.tables["opportunity_evidence"]
    assert (ev["opportunity_id"], ev["insight_id"]) == (opp["id"], "insight-1")
    assert ev["note"]
    assert lines[-1].startswith("✓ Opportunity run created")


def test_the_synthesis_and_its_insights_are_never_modified():
    db = seeded_db()
    before = {t: copy.deepcopy(db.tables[t]) for t in ("syntheses", "insights")}
    run(db)
    assert {t: db.tables[t] for t in before} == before
    assert not any(c[0] == "update" for c in db.calls)


def test_provenance_every_cited_insight_is_in_the_synthesis_and_the_manifest():
    two_insights = output(
        opportunity(),
        opportunity(title="Second opportunity", research_evidence=[{
            "ref": "N2", "note": "Insight 2 also supports this."}]),
    )
    db = seeded_db()
    outcome, llm, _ = run(db, FakeOpportunityLLM(two_insights))
    assert outcome.opportunities == 2
    manifest = db.tables["opportunity_runs"][0]["input_manifest"]
    job_insights = {i["id"] for i in db.tables["insights"] if i["synthesis_id"] == SYNTHESIS_ID}
    for ev in db.tables["opportunity_evidence"]:
        assert ev["insight_id"] in job_insights
        assert ev["insight_id"] in manifest["selected_insight_ids"]
    for opp in db.tables["opportunities"]:
        assert opp["research_job_id"] == db.tables["opportunity_runs"][0]["research_job_id"]


# --- zero-opportunity runs -------------------------------------------------------------------------


def test_a_zero_opportunity_run_is_recorded():
    db = seeded_db()
    outcome, _, _ = run(db, FakeOpportunityLLM(ZERO_OUTPUT))
    assert outcome.status == "created" and outcome.opportunities == 0
    [r] = db.tables["opportunity_runs"]
    assert r["result"] == ZERO_OUTPUT and "connects" in r["result"]["no_opportunities_reason"]
    assert db.tables["opportunities"] == [] and db.tables["opportunity_evidence"] == []
    again, llm2, _ = run(db, FakeOpportunityLLM(ZERO_OUTPUT))
    assert again.status == "exists" and llm2.packs == []


# --- idempotency and versioning ---------------------------------------------------------------------


def test_the_same_run_is_idempotent_and_does_not_call_the_llm_again():
    db = seeded_db()
    first, _, _ = run(db)
    before = snapshot(db)
    second, llm2, lines = run(db)
    assert second.status == "exists" and second.run_id == first.run_id
    assert llm2.packs == []
    assert snapshot(db) == before
    assert any("already exists" in line for line in lines)


def test_a_changed_model_creates_a_new_run_and_keeps_the_old_one():
    db = seeded_db()
    run(db, FakeOpportunityLLM(model="model-a"))
    first = snapshot(db)
    outcome, _, _ = run(db, FakeOpportunityLLM(model="model-b"))
    assert outcome.status == "created" and len(db.tables["opportunity_runs"]) == 2
    assert db.tables["opportunity_runs"][0] == first["opportunity_runs"][0]
    assert {r["model"] for r in db.tables["opportunity_runs"]} == {"model-a", "model-b"}
    assert len(db.tables["opportunities"]) == 2 and len(db.tables["opportunity_evidence"]) == 2


def test_a_changed_prompt_version_creates_a_new_run(monkeypatch):
    db = seeded_db()
    run(db)
    monkeypatch.setattr("research.opportunity_store.PROMPT_VERSION", PROMPT_VERSION + "-next")
    outcome, _, _ = run(db)
    assert outcome.status == "created"
    assert sorted(r["prompt_version"] for r in db.tables["opportunity_runs"]) == sorted(
        [PROMPT_VERSION, PROMPT_VERSION + "-next"])


def test_changed_insights_create_a_new_run():
    db = seeded_db()
    run(db)
    db.tables["insights"].append(insight_row(3))
    outcome, llm, _ = run(db)
    assert outcome.status == "created" and len(db.tables["opportunity_runs"]) == 2
    assert len(llm.packs[0].insight_ids) == 3


def test_losing_a_concurrent_race_is_reported_as_exists_not_as_a_duplicate():
    db = seeded_db()
    run(db)

    class Blind(OpportunityRepository):
        def find_run(self, run_key):
            return None

    llm = FakeOpportunityLLM()
    outcome = run_opportunity_creation(SYNTHESIS_ID, PRODUCT, repository=Blind(db), llm=llm)
    assert outcome.status == "exists" and len(llm.packs) == 1
    assert len(db.tables["opportunity_runs"]) == 1 and len(db.tables["opportunities"]) == 1


def test_the_database_uniqueness_is_the_final_guard():
    db = seeded_db()
    p = pack()
    payload = {"run": {
        "research_job_id": JOB_ID, "synthesis_id": SYNTHESIS_ID, "product_key": "testproduct",
        "product_version": "testproduct-v1", "analysis_type": "opportunity_creation", "model": "m",
        "prompt_version": "p", "schema_version": "s", "run_key": key(p),
        "input_manifest": {}, "result": {}}, "opportunities": []}
    repo = OpportunityRepository(db)
    assert repo.save(payload).status == "created"
    again = repo.save(payload)
    assert again.status == "exists" and len(db.tables["opportunity_runs"]) == 1


# --- atomic persistence ----------------------------------------------------------------------------


def two_opportunity_output():
    return output(opportunity(), opportunity(title="Second"))


def test_a_failure_mid_write_leaves_no_partial_run():
    db = seeded_db()
    before = snapshot(db)
    db.fail_rpc_after_opportunities = 1
    with pytest.raises(PersistenceError, match="simulated failure"):
        run(db, FakeOpportunityLLM(two_opportunity_output()))
    assert snapshot(db) == before
    db.fail_rpc_after_opportunities = None
    assert run(db, FakeOpportunityLLM(two_opportunity_output()))[0].status == "created"


def rpc_payload(**evidence):
    return {"run": {
        "research_job_id": JOB_ID, "synthesis_id": SYNTHESIS_ID, "product_key": "testproduct",
        "product_version": "testproduct-v1", "analysis_type": "opportunity_creation", "model": "m",
        "prompt_version": "p", "schema_version": "s", "run_key": "a" * 64,
        "input_manifest": {}, "result": {}},
        "opportunities": [{
            "title": "t", "audience": "a", "problem": "p", "opportunity": "o", "angle": "ang",
            "marketing_objective": "acquisition", "suggested_formats": ["tutorial"],
            "cta_direction": "c", "product_capability_refs": ["quotes"], "confidence": 0.5,
            "limitations": "l",
            "evidence": [{"insight_id": "insight-1", "note": "n", **evidence}],
        }]}


def test_evidence_integrity_is_enforced_atomically():
    db = seeded_db()
    before = snapshot(db)
    with pytest.raises(PersistenceError, match="foreign key"):
        OpportunityRepository(db).save(rpc_payload(insight_id="insight-nonexistent"))
    assert snapshot(db) == before
    with pytest.raises(PersistenceError, match="note not blank"):
        OpportunityRepository(db).save(rpc_payload(note="  "))
    assert snapshot(db) == before


def test_evidence_from_another_synthesis_is_rejected_and_rolled_back():
    db = seeded_db()
    db.tables["insights"].append({
        "id": "insight-x", "research_job_id": JOB_ID, "synthesis_id": "syn-other",
        **{k: v for k, v in insight_row(9).items() if k not in ("id", "research_job_id", "synthesis_id")},
    })
    before = snapshot(db)
    with pytest.raises(PersistenceError, match="different synthesis"):
        OpportunityRepository(db).save(rpc_payload(insight_id="insight-x"))
    assert snapshot(db) == before


def test_unexpected_rpc_results_are_errors():
    class Odd(RpcFakeDatabase):
        def rpc(self, function, params):
            return {"status": "weird"}

    with pytest.raises(PersistenceError, match="unexpected result"):
        OpportunityRepository(Odd()).save(rpc_payload())


def test_rpc_adapter_calls_the_function_with_the_payload_and_never_leaks_the_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "created", "run_id": "r1", "opportunities": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    db = SupabaseDatabase("https://proj.supabase.co/", SECRET, http_client=client)
    assert OpportunityRepository(db).save({"run": {}, "opportunities": []}).opportunities == 1
    [req] = seen
    assert req.method == "POST" and req.url.path == "/rest/v1/rpc/save_opportunities"
    failing = SupabaseDatabase("https://proj.supabase.co/", SECRET, http_client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(
            500, json={"message": f"boom {SECRET}", "code": "P0001", "details": None, "hint": None}))))
    with pytest.raises(PersistenceError) as info:
        failing.rpc("save_opportunities", {"payload": {}})
    assert SECRET not in str(info.value) and "P0001" in str(info.value)


# --- failure handling: nothing partial ---------------------------------------------------------------


def test_an_llm_failure_stores_nothing():
    db = seeded_db()
    before = snapshot(db)
    with pytest.raises(LLMNetworkError):
        run(db, FakeOpportunityLLM(error=LLMNetworkError("down")))
    assert snapshot(db) == before


def test_an_unresolvable_insight_reference_rejects_the_whole_run_and_stores_nothing():
    bad_output = output(opportunity(research_evidence=[{"ref": "N9", "note": "n"}]))
    db = seeded_db()
    before = snapshot(db)
    with pytest.raises(OpportunityValidationError, match="unknown reference"):
        run(db, FakeOpportunityLLM(bad_output))
    assert snapshot(db) == before


def test_an_out_of_scope_product_claim_rejects_the_whole_run_and_stores_nothing():
    bad_output = output(opportunity(product_fit={
        "capability_refs": ["P1"], "explanation": "Our built-in bookkeeping tracks every expense."}))
    db = seeded_db()
    before = snapshot(db)
    with pytest.raises(OpportunityValidationError, match="product truth") as info:
        run(db, FakeOpportunityLLM(bad_output))
    assert snapshot(db) == before and info.value.raw == bad_output


def test_synthesis_not_found_is_refused_before_any_work():
    db = seeded_db()
    llm = FakeOpportunityLLM()
    with pytest.raises(OpportunitySourceError, match="does not exist"):
        run_opportunity_creation("nope", PRODUCT, repository=OpportunityRepository(db), llm=llm)
    assert llm.packs == [] and db.tables["opportunity_runs"] == []


def test_cli_unknown_product_key_is_refused_before_any_configuration_is_touched(env, monkeypatch, capsys):
    # Product resolution happens at the CLI boundary (main()), before
    # run_opportunity_creation is ever reached - it takes an already-resolved
    # ProductProfile, never a raw key, so this cannot be exercised below main().
    called = []
    monkeypatch.setattr(opportunities, "run_opportunity_creation", lambda *a, **k: called.append(1))
    monkeypatch.setattr(opportunities, "SupabaseDatabase", lambda url, key: (_ for _ in ()).throw(
        AssertionError("must not touch the database for an unknown product")))
    code = opportunities.main(["--synthesis-id", GOOD_SYNTHESIS, "--product", "houseflow"])
    assert code == 2 and called == []
    assert "houseflow" in capsys.readouterr().err


# --- small / empty research ------------------------------------------------------------------------


def test_no_insights_makes_no_llm_call_and_stores_nothing():
    db = seeded_db(insights=[])
    outcome, llm, lines = run(db)
    assert outcome.status == "insufficient_research" and outcome.insight_total == 0
    assert llm.packs == [] and db.tables["opportunity_runs"] == []
    assert any("at least 1" in line for line in lines)


def test_one_insight_is_enough():
    db = seeded_db(insights=[insight_row(1)])
    outcome, _, _ = run(db)
    assert outcome.status == "created" and len(db.tables["opportunity_runs"]) == 1


# --- CLI --------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "llm-secret-not-real")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", SECRET)


def test_cli_rejects_a_non_uuid_synthesis_id(capsys):
    assert opportunities.main(["--synthesis-id", "abc"]) == 2
    assert "must be a UUID" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["LLM_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
def test_cli_missing_configuration_exits_2_without_doing_anything(env, monkeypatch, capsys, missing):
    monkeypatch.delenv(missing)
    called = []
    monkeypatch.setattr(opportunities, "run_opportunity_creation", lambda *a, **k: called.append(1))
    assert opportunities.main(["--synthesis-id", "d8aee101-d48c-4be5-9247-57b33b56fe65"]) == 2
    out = capsys.readouterr()
    assert called == [] and "secret" not in out.out + out.err


GOOD_SYNTHESIS = "d7a385f7-662f-41e9-8027-c232ee64cf83"


def cli(monkeypatch, db, llm, extra_args=()):
    monkeypatch.setattr(opportunities, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(opportunities.AnthropicProvider, "for_opportunities", lambda s: llm)
    return opportunities.main(["--synthesis-id", GOOD_SYNTHESIS, *extra_args])


def cli_db(**kw):
    db = seeded_db(**kw)
    db.tables["syntheses"][0]["id"] = GOOD_SYNTHESIS
    for row in db.tables["insights"]:
        row["synthesis_id"] = GOOD_SYNTHESIS
    return db


def test_cli_success_prints_progress_and_no_secrets(env, monkeypatch, capsys):
    db = cli_db()
    assert cli(monkeypatch, db, FakeOpportunityLLM()) == 0
    out = capsys.readouterr()
    assert "Opportunity Creation" in out.out and "Opportunity run created" in out.out
    assert "secret" not in out.out + out.err and len(db.tables["opportunity_runs"]) == 1


def test_cli_rerun_is_a_successful_noop(env, monkeypatch, capsys):
    db = cli_db()
    assert cli(monkeypatch, db, FakeOpportunityLLM()) == 0
    assert cli(monkeypatch, db, FakeOpportunityLLM()) == 0
    assert "already exists" in capsys.readouterr().out and len(db.tables["opportunity_runs"]) == 1


def test_cli_insufficient_research_exits_4(env, monkeypatch, capsys):
    llm = FakeOpportunityLLM()
    assert cli(monkeypatch, cli_db(insights=[]), llm) == 4
    assert llm.packs == [] and "Not enough research" in capsys.readouterr().out


def test_cli_unknown_synthesis_exits_1_and_llm_failure_exits_3(env, monkeypatch, capsys):
    db = cli_db()
    db.tables["syntheses"] = []
    assert cli(monkeypatch, db, FakeOpportunityLLM()) == 1
    assert "Cannot create opportunities" in capsys.readouterr().err
    assert cli(monkeypatch, cli_db(), FakeOpportunityLLM(error=LLMNetworkError("down"))) == 3
    err = capsys.readouterr().err
    assert "LLMNetworkError" in err and "secret" not in err


def test_cli_defaults_to_the_tradeflow_product(env, monkeypatch):
    seen = []
    monkeypatch.setattr(opportunities, "SupabaseDatabase", lambda url, key: cli_db())
    monkeypatch.setattr(opportunities.AnthropicProvider, "for_opportunities", lambda s: FakeOpportunityLLM())
    monkeypatch.setattr(
        opportunities, "run_opportunity_creation",
        lambda synthesis_id, product, **kw: seen.append(product) or opportunities.OpportunityRunOutcome(
            status="created", run_id="r", opportunities=0, insight_total=0),
    )
    opportunities.main(["--synthesis-id", GOOD_SYNTHESIS])
    assert [p.key for p in seen] == ["tradeflow"]


# --- migration ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sql():
    [path] = MIGRATIONS.glob("*_create_opportunity_runs_and_reshape_opportunities.sql")
    stripped = re.sub(r"--[^\n]*", "", path.read_text())
    return re.sub(r"\s+", " ", stripped).lower()


def test_migration_creates_an_immutable_run_table_with_a_unique_run_key(sql):
    assert "create table public.opportunity_runs" in sql
    for col in ("research_job_id uuid not null", "synthesis_id uuid not null", "product_key text not null",
                "product_version text not null", "model text not null", "prompt_version text not null",
                "schema_version text not null", "run_key text not null", "input_manifest jsonb not null",
                "result jsonb not null", "created_at timestamptz not null default now()"):
        assert col in sql, col
    assert "constraint opportunity_runs_run_key_key unique (run_key)" in sql
    assert "run_key ~ '^[0-9a-f]{64}$'" in sql
    assert "unique (id, research_job_id)" in sql
    assert "create trigger opportunity_runs_immutable before update" in sql


def test_migration_reshapes_opportunities_and_keeps_job_and_run_consistent(sql):
    assert "raise exception 'opportunities contains data" in sql  # guarded reshaping
    assert "rename column target_audience to audience" in sql
    assert "rename column pain_point to problem" in sql
    for dropped in ("drop column insight_id", "drop column description", "drop column suggested_format",
                    "drop column suggested_hook", "drop column reason", "drop column status",
                    "drop column updated_at"):
        assert dropped in sql, dropped
    assert "add column opportunity_run_id" in sql
    assert "foreign key (opportunity_run_id, research_job_id) references public.opportunity_runs (id, research_job_id)" in sql
    assert "confidence >= 0 and confidence <= 1" in sql
    assert "create trigger opportunities_immutable before update" in sql


def test_migration_evidence_table_has_real_foreign_keys_and_invariants(sql):
    body = sql.split("create table public.opportunity_evidence")[1].split("comment on table")[0]
    assert "opportunity_id uuid not null" in body and "on delete cascade" in body
    assert re.search(r"insight_id uuid not null\s+references public\.insights \(id\) on update cascade on delete restrict", body)
    assert "btrim(note) <> ''" in body
    assert "unique (opportunity_id, insight_id)" in body


def test_migration_only_grants_dashboard_read_access_to_the_new_tables(sql):
    policies = re.findall(r"create policy [^;]+;", sql)
    assert len(policies) == 2
    for p in policies:
        assert "for select to anon, authenticated using (true)" in p
    assert not re.search(r"for (insert|update|delete|all)", sql)
    assert "enable row level security" in sql


def test_migration_writes_through_one_atomic_function_only_the_service_role_can_call(sql):
    assert "create function public.save_opportunities(payload jsonb) returns jsonb" in sql
    assert "on conflict (run_key) do nothing" in sql
    assert "revoke all on function public.save_opportunities(jsonb) from public, anon, authenticated" in sql
    assert "grant execute on function public.save_opportunities(jsonb) to service_role" in sql
    assert "cited insight belongs to a different synthesis or research job" in sql
    assert "policy" not in sql.split("create function public.save_opportunities")[1]


def test_earlier_migrations_are_not_rewritten_by_this_chapter():
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    # Exactly six migrations predate this chapter's; it lands immediately
    # after them. A later chapter (e.g. production) may add further
    # migrations after this one without this assertion becoming false.
    assert names.index(next(n for n in names if "create_opportunity_runs" in n)) == 6
