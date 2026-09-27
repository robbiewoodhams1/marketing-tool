-- Opportunity Creation v1.
--
--   research_job
--     └── syntheses
--           └── insights
--   (synthesis_id, research_job_id)
--     └── opportunity_runs      one run of the opportunity-creation engine, for
--         │                     one synthesis and one product profile (immutable)
--         └── opportunities     one concrete marketing opportunity            (immutable)
--               └── opportunity_evidence   real FKs to the insights that justify it
--
-- Opportunity Creation is the bridge between market research (syntheses/
-- insights) and content production: given what research found AND what a
-- specific product actually does, which opportunities are worth producing?
-- Product context itself is NOT a database table - it is versioned code
-- (research.product_context / research.products), the same way a prompt
-- version is code, not a row. Only its identity (product_key, product_version)
-- and a deterministic snapshot (input_manifest) are stored here, so a run is
-- always traceable to exactly which product facts it saw.
--
-- opportunity_runs is the authoritative record that "this analytical process
-- was run against this exact synthesis and product profile". It exists even
-- when the run found no viable opportunity (result.no_opportunities_reason).
--
-- run_key (SHA-256 of the canonical run configuration, built by the research
-- service) is UNIQUE: the database is the final guarantee that an identical
-- run is never stored twice, even under concurrent execution. A changed
-- model, prompt version, schema version, pack version, product profile
-- version or selected insight set yields a different run_key and therefore a
-- new, additive run.
--
-- Writes go through save_opportunities(), which stores a fully validated
-- payload in ONE transaction (all or nothing). The tables are read-only to
-- the dashboard roles.
--
-- `opportunities` already exists (created outside this repo's tracked
-- migrations, alongside `insights`) as a flat, pre-v1 prototype shape with no
-- rows in it. It is reshaped here exactly as the previous chapter reshaped
-- `insights`: guarded on being empty, so no backfill is needed or attempted.

do $$
begin
  if exists (select 1 from public.opportunities) then
    raise exception 'opportunities contains data; refusing to reshape it';
  end if;
end $$;

-- --- opportunity_runs -----------------------------------------------------------

create table public.opportunity_runs (
  id              uuid primary key default gen_random_uuid(),
  research_job_id uuid not null
                    references public.research_jobs (id) on update cascade on delete restrict,
  synthesis_id    uuid not null,
  product_key     text not null,
  product_version text not null,
  analysis_type   text not null,
  model           text not null,
  prompt_version  text not null,
  schema_version  text not null,
  run_key         text not null,
  input_manifest  jsonb not null,
  result          jsonb not null,
  created_at      timestamptz not null default now(),
  constraint opportunity_runs_run_key_key unique (run_key),
  constraint opportunity_runs_run_key_format check (run_key ~ '^[0-9a-f]{64}$'),
  -- an opportunity run and its synthesis cannot point at different research jobs
  constraint opportunity_runs_synthesis_job_fkey
    foreign key (synthesis_id, research_job_id)
    references public.syntheses (id, research_job_id)
    on update cascade on delete cascade,
  -- target of the composite FK that keeps opportunities.research_job_id consistent
  constraint opportunity_runs_id_research_job_id_key unique (id, research_job_id)
);

comment on table public.opportunity_runs is
  'One row is one opportunity-creation run over a synthesis and product profile (immutable)';

create index opportunity_runs_research_job_id_created_at_idx
  on public.opportunity_runs (research_job_id, created_at desc);
create index opportunity_runs_synthesis_id_idx on public.opportunity_runs (synthesis_id);

-- --- opportunities (empty; reshaped) ---------------------------------------------
--
-- Previous shape (all nullable, no rows exist):
--   id, created_at, insight_id, title, description, target_audience,
--   pain_point, suggested_format, suggested_hook, reason, status, updated_at,
--   research_job_id
--
-- insight_id (a single FK) is replaced by opportunity_evidence, a join table:
-- an opportunity may rest on more than one insight (Part 5's provenance
-- chain), and one insight may justify more than one opportunity.

alter table public.opportunities drop column insight_id;
alter table public.opportunities drop column description;
alter table public.opportunities drop column suggested_format;
alter table public.opportunities drop column suggested_hook;
alter table public.opportunities drop column reason;
alter table public.opportunities drop column status;
alter table public.opportunities drop column updated_at;  -- opportunities are immutable
-- research_job_id had a stray `gen_random_uuid()` default despite being a
-- foreign key (a leftover from the original, untracked table definition).
alter table public.opportunities alter column research_job_id drop default;

alter table public.opportunities rename column target_audience to audience;
alter table public.opportunities rename column pain_point to problem;

alter table public.opportunities
  add column opportunity_run_id       uuid  not null,
  add column opportunity              text  not null,  -- the concrete thing that could be made
  add column angle                    text  not null,
  add column marketing_objective      text  not null,
  add column suggested_formats        jsonb not null,
  add column cta_direction            text  not null,
  add column product_capability_refs  jsonb not null,  -- resolved product capability keys (code-verified)
  add column confidence               numeric not null,
  add column limitations              text  not null;

alter table public.opportunities
  alter column title           set not null,
  alter column audience        set not null,
  alter column problem          set not null,
  alter column research_job_id set not null;

alter table public.opportunities
  add constraint opportunities_confidence_range
    check (confidence >= 0 and confidence <= 1),
  -- an opportunity and its run cannot point at different research jobs
  add constraint opportunities_run_job_fkey
    foreign key (opportunity_run_id, research_job_id)
    references public.opportunity_runs (id, research_job_id)
    on update cascade on delete cascade;

comment on table public.opportunities is
  'One row is one concrete marketing opportunity produced by an opportunity-creation run (immutable)';

create index opportunities_opportunity_run_id_idx on public.opportunities (opportunity_run_id);

-- --- opportunity_evidence ---------------------------------------------------------

create table public.opportunity_evidence (
  id             uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null
                   references public.opportunities (id) on update cascade on delete cascade,
  insight_id     uuid not null
                   references public.insights (id) on update cascade on delete restrict,
  note           text not null,
  created_at     timestamptz not null default now(),
  constraint opportunity_evidence_note_not_blank check (btrim(note) <> ''),
  constraint opportunity_evidence_unique_pair unique (opportunity_id, insight_id)
);

comment on table public.opportunity_evidence is
  'One row is one research insight cited as justification for one opportunity (immutable)';

-- Insights may not be deleted while an opportunity cites them (ON DELETE
-- RESTRICT above); a deleted opportunity/run takes its evidence rows with it.
create index opportunity_evidence_opportunity_id_idx on public.opportunity_evidence (opportunity_id);
create index opportunity_evidence_insight_id_idx on public.opportunity_evidence (insight_id);

-- --- immutability -----------------------------------------------------------------
-- public.forbid_update() already exists (created alongside syntheses/insights).

create trigger opportunity_runs_immutable before update on public.opportunity_runs
  for each row execute function public.forbid_update();
create trigger opportunities_immutable before update on public.opportunities
  for each row execute function public.forbid_update();
create trigger opportunity_evidence_immutable before update on public.opportunity_evidence
  for each row execute function public.forbid_update();

-- --- RLS: read-only for the dashboard roles, same shape as syntheses/insights ------

alter table public.opportunity_runs enable row level security;
alter table public.opportunity_evidence enable row level security;
-- opportunities already has RLS enabled and "dashboard can read opportunities".

create policy "dashboard can read opportunity runs"
  on public.opportunity_runs for select to anon, authenticated using (true);
create policy "dashboard can read opportunity evidence"
  on public.opportunity_evidence for select to anon, authenticated using (true);

-- --- atomic write -------------------------------------------------------------------
--
-- payload = {
--   "run": {research_job_id, synthesis_id, product_key, product_version,
--           analysis_type, model, prompt_version, schema_version, run_key,
--           input_manifest, result},
--   "opportunities": [{title, audience, problem, opportunity, angle,
--                      marketing_objective, suggested_formats, cta_direction,
--                      product_capability_refs, confidence, limitations,
--                      evidence: [{insight_id, note}]}]
-- }
--
-- The payload has ALREADY been validated by the research service (schema,
-- references, product truth, not-finished-content); this function only stores
-- it atomically. Its one check is a referential-integrity backstop: cited
-- insights must belong to the run's own synthesis and research job.
--
-- Returns {"status": "created"|"exists", "run_id": ..., "opportunities": n}.
-- ON CONFLICT (run_key) DO NOTHING makes a concurrent duplicate wait for the
-- first transaction and then report "exists" instead of erroring or duplicating.

create function public.save_opportunities(payload jsonb) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  r            jsonb := payload -> 'run';
  new_id       uuid;
  existing_id  uuid;
  opp          jsonb;
  ev           jsonb;
  new_opp      uuid;
  n            integer := 0;
begin
  if r is null or jsonb_typeof(payload -> 'opportunities') is distinct from 'array' then
    raise exception 'save_opportunities: malformed payload';
  end if;

  insert into public.opportunity_runs
    (research_job_id, synthesis_id, product_key, product_version, analysis_type, model,
     prompt_version, schema_version, run_key, input_manifest, result)
  values
    ((r ->> 'research_job_id')::uuid, (r ->> 'synthesis_id')::uuid, r ->> 'product_key',
     r ->> 'product_version', r ->> 'analysis_type', r ->> 'model', r ->> 'prompt_version',
     r ->> 'schema_version', r ->> 'run_key', r -> 'input_manifest', r -> 'result')
  on conflict (run_key) do nothing
  returning id into new_id;

  if new_id is null then
    select id into existing_id from public.opportunity_runs where run_key = r ->> 'run_key';
    return jsonb_build_object('status', 'exists', 'run_id', existing_id);
  end if;

  for opp in select * from jsonb_array_elements(payload -> 'opportunities') loop
    insert into public.opportunities
      (opportunity_run_id, research_job_id, title, audience, problem, opportunity, angle,
       marketing_objective, suggested_formats, cta_direction, product_capability_refs,
       confidence, limitations)
    values
      (new_id, (r ->> 'research_job_id')::uuid, opp ->> 'title', opp ->> 'audience',
       opp ->> 'problem', opp ->> 'opportunity', opp ->> 'angle', opp ->> 'marketing_objective',
       coalesce(opp -> 'suggested_formats', '[]'::jsonb), opp ->> 'cta_direction',
       coalesce(opp -> 'product_capability_refs', '[]'::jsonb),
       (opp ->> 'confidence')::numeric, opp ->> 'limitations')
    returning id into new_opp;
    n := n + 1;

    for ev in select * from jsonb_array_elements(coalesce(opp -> 'evidence', '[]'::jsonb)) loop
      insert into public.opportunity_evidence (opportunity_id, insight_id, note)
      values (new_opp, (ev ->> 'insight_id')::uuid, ev ->> 'note');
    end loop;
  end loop;

  if exists (
    select 1
    from public.opportunity_evidence oe
    join public.opportunities o on o.id = oe.opportunity_id
    join public.insights i on i.id = oe.insight_id
    where o.opportunity_run_id = new_id
      and (i.research_job_id is distinct from o.research_job_id
           or i.synthesis_id is distinct from (r ->> 'synthesis_id')::uuid)
  ) then
    raise exception 'save_opportunities: cited insight belongs to a different synthesis or research job';
  end if;

  return jsonb_build_object('status', 'created', 'run_id', new_id, 'opportunities', n);
end $$;

-- Only the research service (service role) may write.
revoke all on function public.save_opportunities(jsonb) from public, anon, authenticated;
grant execute on function public.save_opportunities(jsonb) to service_role;
