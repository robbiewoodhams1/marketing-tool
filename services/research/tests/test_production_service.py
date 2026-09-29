import copy
import re
from pathlib import Path

import httpx
import pytest
from production_fixtures import (
    GOOD_OUTPUT,
    OPPORTUNITY_ID,
    FakeProductionLLM,
    RpcFakeDatabase,
    opportunity_row,
    production_output,
    scene,
    seeded_db,
)

from research import production
from research.llm import LLMNetworkError
from research.production import ProductionSourceError, run_production
from research.production_pack import build_production_pack
from research.production_prompt import PROMPT_VERSION
from research.production_schema import ProductionValidationError
from research.production_store import ProductionRepository, compute_run_key
from research.persistence import PersistenceError, SupabaseDatabase

SECRET = "sr-secret-not-real"
MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def run(db, llm=None, **kw):
    lines = []
    llm = llm or FakeProductionLLM()
    outcome = run_production(
        OPPORTUNITY_ID, repository=ProductionRepository(db), llm=llm, progress=lines.append, **kw
    )
    return outcome, llm, lines


def snapshot(db):
    return copy.deepcopy(db.tables)


# --- run identity ------------------------------------------------------------------------------


def pack():
    from opportunity_fixtures import PRODUCT

    return build_production_pack(
        opportunity=opportunity_row(),
        research_context=[{"id": "insight-1", "title": "t", "statement": "s", "limitations": "l"}],
        product=PRODUCT,
    )


PACK = pack()


def key(p=PACK, **kw):
    return compute_run_key(**{"opportunity_id": OPPORTUNITY_ID, "model": "m", "pack": p, **kw})


def test_run_key_is_a_sha256_hex_digest_and_stable():
    k = key()
    assert re.fullmatch(r"[0-9a-f]{64}", k) and k == key() == key(pack())


@pytest.mark.parametrize("change", [
    {"model": "other-model"},
    {"prompt_version": PROMPT_VERSION + "-next"},
    {"schema_version": "production-v2"},
    {"analysis_type": "other_analysis"},
    {"opportunity_id": "opp-2"},
])
def test_changing_any_configuration_input_changes_the_run_key(change):
    assert key(**change) != key()


def test_changing_the_product_profile_changes_the_run_key():
    from opportunity_fixtures import product

    other_pack = build_production_pack(
        opportunity=opportunity_row(),
        research_context=[{"id": "insight-1", "title": "t", "statement": "s", "limitations": "l"}],
        product=product(version="testproduct-v2"),
    )
    assert key(other_pack) != key()


def test_the_opportunity_content_itself_is_not_part_of_the_run_key():
    # The opportunity is immutable, so its id alone stands in for its content;
    # a differently-worded opportunity_row() with the SAME id yields the same key.
    from opportunity_fixtures import PRODUCT

    other_pack = build_production_pack(
        opportunity=opportunity_row(title="A different title"), research_context=[], product=PRODUCT,
    )
    assert key(other_pack) == key()


# --- the happy path ------------------------------------------------------------------------------


def test_a_complete_production_is_persisted_with_provenance():
    db = seeded_db()
    outcome, llm, lines = run(db)
    assert outcome.status == "created"

    [r] = db.tables["production_runs"]
    assert (r["opportunity_id"], r["research_job_id"], r["model"]) == (
        OPPORTUNITY_ID, "job-1", "production-model")
    assert r["product_key"] == "tradeflow" and r["product_version"] == "tradeflow-v1"
    assert r["prompt_version"] == PROMPT_VERSION and r["schema_version"] == "production-v1"
    assert re.fullmatch(r"[0-9a-f]{64}", r["run_key"]) and r["run_key"] == outcome.run_key
    assert r["input_manifest"] == llm.packs[0].manifest
    assert r["result"] == GOOD_OUTPUT

    [prod] = db.tables["productions"]
    assert (prod["production_run_id"], prod["opportunity_id"]) == (r["id"], OPPORTUNITY_ID)
    assert prod["target_audience"] == "UK sole traders who repeat similar jobs"  # copied from opportunity
    assert prod["objective"] == "activation"  # copied from opportunity.marketing_objective
    assert prod["platform"] == "tiktok" and prod["estimated_duration_seconds"] == 18
    assert len(prod["scenes"]) == 2
    assert prod["script"].startswith(GOOD_OUTPUT["hook"])
    assert GOOD_OUTPUT["cta"] in prod["script"]
    # P1/P2 resolve against the REAL TradeFlow profile here (this test goes
    # through the registry): quotes, jobs - not testproduct's quotes/reusable_templates.
    assert prod["product_capability_refs"] == ["quotes", "jobs"]
    assert lines[-1].startswith("✓ Production run created")


def test_the_opportunity_and_its_provenance_are_never_modified():
    db = seeded_db()
    before = {t: copy.deepcopy(db.tables[t]) for t in ("opportunities", "opportunity_runs", "insights", "opportunity_evidence")}
    run(db)
    assert {t: db.tables[t] for t in before} == before
    assert not any(c[0] == "update" for c in db.calls)


def test_script_is_assembled_from_hook_scenes_and_cta_in_order():
    db = seeded_db()
    run(db)
    [prod] = db.tables["productions"]
    parts = [p for p in prod["script"].split("\n\n")]
    assert parts[0] == GOOD_OUTPUT["hook"]
    assert parts[-1] == GOOD_OUTPUT["cta"]
    assert parts[1] == GOOD_OUTPUT["scenes"][0]["voiceover"]


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
    run(db, FakeProductionLLM(model="model-a"))
    first = snapshot(db)
    outcome, _, _ = run(db, FakeProductionLLM(model="model-b"))
    assert outcome.status == "created" and len(db.tables["production_runs"]) == 2
    assert db.tables["production_runs"][0] == first["production_runs"][0]
    assert {r["model"] for r in db.tables["production_runs"]} == {"model-a", "model-b"}
    assert len(db.tables["productions"]) == 2


def test_a_changed_prompt_version_creates_a_new_run(monkeypatch):
    db = seeded_db()
    run(db)
    monkeypatch.setattr("research.production_store.PROMPT_VERSION", PROMPT_VERSION + "-next")
    outcome, _, _ = run(db)
    assert outcome.status == "created"
    assert sorted(r["prompt_version"] for r in db.tables["production_runs"]) == sorted(
        [PROMPT_VERSION, PROMPT_VERSION + "-next"])


def test_losing_a_concurrent_race_is_reported_as_exists_not_as_a_duplicate():
    db = seeded_db()
    run(db)

    class Blind(ProductionRepository):
        def find_run(self, run_key):
            return None

    llm = FakeProductionLLM()
    outcome = run_production(OPPORTUNITY_ID, repository=Blind(db), llm=llm)
    assert outcome.status == "exists" and len(llm.packs) == 1
    assert len(db.tables["production_runs"]) == 1 and len(db.tables["productions"]) == 1


def test_the_database_uniqueness_is_the_final_guard():
    payload = {"run": {
        "opportunity_id": OPPORTUNITY_ID, "research_job_id": "job-1", "product_key": "testproduct",
        "product_version": "testproduct-v1", "analysis_type": "production", "model": "m",
        "prompt_version": "p", "schema_version": "s", "run_key": key(),
        "input_manifest": {}, "result": {}},
        "production": {**production_output(), "target_audience": "a", "objective": "o",
                       "script": "s", "product_capability_refs": ["quotes"]}}
    db = seeded_db()
    repo = ProductionRepository(db)
    assert repo.save(payload).status == "created"
    again = repo.save(payload)
    assert again.status == "exists" and len(db.tables["production_runs"]) == 1


# --- atomic persistence ----------------------------------------------------------------------------


def rpc_payload(**overrides):
    production = {**production_output(), "target_audience": "a", "objective": "o", "script": "s",
                  "product_capability_refs": ["quotes"]}
    production.update(overrides)
    return {"run": {
        "opportunity_id": OPPORTUNITY_ID, "research_job_id": "job-1", "product_key": "testproduct",
        "product_version": "testproduct-v1", "analysis_type": "production", "model": "m",
        "prompt_version": "p", "schema_version": "s", "run_key": "a" * 64,
        "input_manifest": {}, "result": {}}, "production": production}


def test_a_bad_duration_is_rejected_atomically_and_leaves_no_run_row():
    db = seeded_db()
    before = snapshot(db)
    with pytest.raises(PersistenceError, match="duration_seconds range"):
        ProductionRepository(db).save(rpc_payload(estimated_duration_seconds=5))
    assert snapshot(db) == before  # the run row is rolled back too, not just the production


def test_unexpected_rpc_results_are_errors():
    class Odd(RpcFakeDatabase):
        def rpc(self, function, params):
            return {"status": "weird"}

    with pytest.raises(PersistenceError, match="unexpected result"):
        ProductionRepository(Odd()).save(rpc_payload())


def test_rpc_adapter_calls_the_function_with_the_payload_and_never_leaks_the_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "created", "run_id": "r1", "production_id": "p1"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    db = SupabaseDatabase("https://proj.supabase.co/", SECRET, http_client=client)
    assert ProductionRepository(db).save({"run": {}, "production": {}}).production_id == "p1"
    [req] = seen
    assert req.method == "POST" and req.url.path == "/rest/v1/rpc/save_production"
    failing = SupabaseDatabase("https://proj.supabase.co/", SECRET, http_client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(
            500, json={"message": f"boom {SECRET}", "code": "P0001", "details": None, "hint": None}))))
    with pytest.raises(PersistenceError) as info:
        failing.rpc("save_production", {"payload": {}})
    assert SECRET not in str(info.value) and "P0001" in str(info.value)


# --- failure handling: nothing partial ---------------------------------------------------------------


def test_an_llm_failure_stores_nothing():
    db = seeded_db()
    before = snapshot(db)
    with pytest.raises(LLMNetworkError):
        run(db, FakeProductionLLM(error=LLMNetworkError("down")))
    assert snapshot(db) == before


def test_an_out_of_scope_product_claim_rejects_the_whole_run_and_stores_nothing():
    bad = production_output(scenes=[
        scene(voiceover="TradeFlow automatically files your tax return."), scene(number=2)])
    db = seeded_db()
    before = snapshot(db)
    with pytest.raises(ProductionValidationError, match="product truth") as info:
        run(db, FakeProductionLLM(bad))
    assert snapshot(db) == before and info.value.raw == bad


def test_an_unresolvable_capability_reference_rejects_the_whole_run_and_stores_nothing():
    from production_fixtures import product_claims as make_product_claims

    bad = production_output(product_claims=make_product_claims(capability_refs=("P9",)))
    db = seeded_db()
    before = snapshot(db)
    with pytest.raises(ProductionValidationError, match="unknown reference"):
        run(db, FakeProductionLLM(bad))
    assert snapshot(db) == before


def test_opportunity_not_found_is_refused_before_any_work():
    db = seeded_db()
    llm = FakeProductionLLM()
    with pytest.raises(ProductionSourceError, match="does not exist"):
        run_production("nope", repository=ProductionRepository(db), llm=llm)
    assert llm.packs == [] and db.tables["production_runs"] == []


def test_a_dangling_opportunity_run_reference_is_a_clean_persistence_error():
    db = seeded_db()
    db.tables["opportunity_runs"] = []  # the opportunity's run row is gone
    llm = FakeProductionLLM()
    with pytest.raises(PersistenceError, match="does not exist"):
        run_production(OPPORTUNITY_ID, repository=ProductionRepository(db), llm=llm)
    assert llm.packs == []


# --- CLI --------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "llm-secret-not-real")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", SECRET)


def test_cli_rejects_a_non_uuid_opportunity_id(capsys):
    assert production.main(["--opportunity-id", "abc"]) == 2
    assert "must be a UUID" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["LLM_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
def test_cli_missing_configuration_exits_2_without_doing_anything(env, monkeypatch, capsys, missing):
    monkeypatch.delenv(missing)
    called = []
    monkeypatch.setattr(production, "run_production", lambda *a, **k: called.append(1))
    assert production.main(["--opportunity-id", "8e610e89-87e7-4838-a7f7-222d7fceda0b"]) == 2
    out = capsys.readouterr()
    assert called == [] and "secret" not in out.out + out.err


GOOD_OPPORTUNITY = "8e610e89-87e7-4838-a7f7-222d7fceda0b"


def cli(monkeypatch, db, llm):
    monkeypatch.setattr(production, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(production.AnthropicProvider, "for_production", lambda s: llm)
    return production.main(["--opportunity-id", GOOD_OPPORTUNITY])


def cli_db(**kw):
    db = seeded_db(**kw)
    db.tables["opportunities"][0]["id"] = GOOD_OPPORTUNITY
    for row in db.tables["opportunity_evidence"]:
        row["opportunity_id"] = GOOD_OPPORTUNITY
    return db


def test_cli_success_prints_progress_and_no_secrets(env, monkeypatch, capsys):
    db = cli_db()
    assert cli(monkeypatch, db, FakeProductionLLM()) == 0
    out = capsys.readouterr()
    assert "Production" in out.out and "Production run created" in out.out
    assert "secret" not in out.out + out.err and len(db.tables["production_runs"]) == 1


def test_cli_rerun_is_a_successful_noop(env, monkeypatch, capsys):
    db = cli_db()
    assert cli(monkeypatch, db, FakeProductionLLM()) == 0
    assert cli(monkeypatch, db, FakeProductionLLM()) == 0
    assert "already exists" in capsys.readouterr().out and len(db.tables["production_runs"]) == 1


def test_cli_unknown_opportunity_exits_1_and_llm_failure_exits_3(env, monkeypatch, capsys):
    db = cli_db()
    db.tables["opportunities"] = []
    assert cli(monkeypatch, db, FakeProductionLLM()) == 1
    assert "Cannot produce" in capsys.readouterr().err
    assert cli(monkeypatch, cli_db(), FakeProductionLLM(error=LLMNetworkError("down"))) == 3
    err = capsys.readouterr().err
    assert "LLMNetworkError" in err and "secret" not in err


def test_cli_invalid_model_output_fails_safely(env, monkeypatch, capsys):
    bad = production_output(product_claims={"capability_refs": ["P9"], "explanation": "e"})
    assert cli(monkeypatch, cli_db(), FakeProductionLLM(bad)) == 3
    err = capsys.readouterr().err
    assert "unknown reference" in err and "secret" not in err


# --- migration ----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sql():
    [path] = MIGRATIONS.glob("*_create_production_runs_and_productions.sql")
    stripped = re.sub(r"--[^\n]*", "", path.read_text())
    return re.sub(r"\s+", " ", stripped).lower()


def test_migration_creates_an_immutable_run_table_with_a_unique_run_key(sql):
    assert "create table public.production_runs" in sql
    for col in ("opportunity_id uuid not null", "research_job_id uuid not null",
                "product_key text not null", "product_version text not null",
                "model text not null", "prompt_version text not null",
                "schema_version text not null", "run_key text not null",
                "input_manifest jsonb not null", "result jsonb not null",
                "created_at timestamptz not null default now()"):
        assert col in sql, col
    assert "constraint production_runs_run_key_key unique (run_key)" in sql
    assert "run_key ~ '^[0-9a-f]{64}$'" in sql
    assert "unique (id, opportunity_id)" in sql
    assert "create trigger production_runs_immutable before update" in sql


def test_migration_creates_productions_with_a_duration_check_and_consistent_fk(sql):
    body = sql.split("create table public.productions")[1].split("comment on table")[0]
    assert "estimated_duration_seconds integer not null" in body
    assert "check (estimated_duration_seconds between 15 and 65)" in body
    assert "foreign key (production_run_id, opportunity_id) references public.production_runs (id, opportunity_id)" in body
    assert "create trigger productions_immutable before update" in sql


def test_migration_only_grants_dashboard_read_access(sql):
    policies = re.findall(r"create policy [^;]+;", sql)
    assert len(policies) == 2
    for p in policies:
        assert "for select to anon, authenticated using (true)" in p
    assert not re.search(r"for (insert|update|delete|all)", sql)
    assert "enable row level security" in sql


def test_migration_writes_through_one_atomic_function_only_the_service_role_can_call(sql):
    assert "create function public.save_production(payload jsonb) returns jsonb" in sql
    assert "on conflict (run_key) do nothing" in sql
    assert "revoke all on function public.save_production(jsonb) from public, anon, authenticated" in sql
    assert "grant execute on function public.save_production(jsonb) to service_role" in sql
    assert "policy" not in sql.split("create function public.save_production")[1]


def test_earlier_migrations_are_not_rewritten_by_this_chapter():
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    # Exactly seven migrations predate this chapter's; it lands immediately
    # after them. A later chapter (e.g. media generation) may add further
    # migrations after this one without this assertion becoming false.
    assert names.index(next(n for n in names if "create_production_runs" in n)) == 7
