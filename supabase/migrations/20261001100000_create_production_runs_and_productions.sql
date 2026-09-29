-- Production v1.
--
--   opportunity_runs
--     └── opportunities
--   (opportunity_id)
--     └── production_runs      one run of the production engine, for one
--         │                    opportunity and product profile (immutable)
--         └── productions      one short-form video package               (immutable)
--
-- Production is the next stage after Opportunity Creation: given ONE
-- persisted opportunity (the creative brief) and the product profile it was
-- created for, produce a concrete, ready-to-shoot short-form video
-- specification. Product context itself is NOT a database table here either
-- - same as Opportunity Creation, it is versioned code
-- (research.product_context / research.products); only its identity
-- (product_key, product_version) and a deterministic snapshot
-- (input_manifest) are stored.
--
-- Unlike syntheses->insights or opportunity_runs->opportunities, one
-- production run produces exactly ONE production in v1 (there is no
-- "insufficient input" or "zero result" case: every opportunity is enough to
-- attempt a production). The run/production split is kept anyway, mirroring
-- the established shape, because a future version may produce more than one
-- variant (e.g. per platform) from a single run.
--
-- Provenance is NOT duplicated: a production traces back to its opportunity's
-- own evidence (opportunity_evidence -> insights -> insight_evidence -> raw
-- content/comments) via opportunities.id alone. No new evidence-join table is
-- needed - see research.production_pack, which loads that chain as read-only
-- background context (never something the model cites new references into).
--
-- run_key (SHA-256 of the canonical run configuration, built by the research
-- service) is UNIQUE: the database is the final guarantee that an identical
-- run is never stored twice, even under concurrent execution.
--
-- Writes go through save_production(), which stores a fully validated
-- payload in ONE transaction (all or nothing). The tables are read-only to
-- the dashboard roles.

-- --- production_runs --------------------------------------------------------------

create table public.production_runs (
  id              uuid primary key default gen_random_uuid(),
  opportunity_id  uuid not null
                    references public.opportunities (id) on update cascade on delete restrict,
  research_job_id uuid not null
                    references public.research_jobs (id) on update cascade on delete restrict,
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
  constraint production_runs_run_key_key unique (run_key),
  constraint production_runs_run_key_format check (run_key ~ '^[0-9a-f]{64}$'),
  -- target of the composite FK that keeps productions.opportunity_id consistent
  constraint production_runs_id_opportunity_id_key unique (id, opportunity_id)
);

comment on table public.production_runs is
  'One row is one production run over an opportunity and product profile (immutable)';

create index production_runs_opportunity_id_idx on public.production_runs (opportunity_id);
create index production_runs_research_job_id_created_at_idx
  on public.production_runs (research_job_id, created_at desc);

-- --- productions -------------------------------------------------------------------

create table public.productions (
  id                        uuid primary key default gen_random_uuid(),
  production_run_id         uuid not null,
  opportunity_id            uuid not null,
  title                     text not null,
  hook                      text not null,
  target_audience           text not null,  -- copied from the opportunity, not model output
  objective                 text not null,  -- copied from the opportunity, not model output
  platform                  text not null,
  estimated_duration_seconds integer not null,
  scenes                    jsonb not null,  -- [{number, duration_seconds, purpose, voiceover,
                                              --   on_screen_text, visual_direction,
                                              --   product_capability_refs, transition}]
  script                    text not null,  -- code-assembled: hook + each scene's voiceover + cta
  cta                       text not null,
  caption                   text not null,
  hashtags                  jsonb not null,  -- string[]; may be empty
  product_capability_refs   jsonb not null,  -- resolved product capability keys (code-verified)
  limitations               text not null,
  created_at                timestamptz not null default now(),
  constraint productions_duration_range
    check (estimated_duration_seconds between 15 and 65),
  -- a production and its run cannot point at different opportunities
  constraint productions_run_opportunity_fkey
    foreign key (production_run_id, opportunity_id)
    references public.production_runs (id, opportunity_id)
    on update cascade on delete cascade
);

comment on table public.productions is
  'One row is one short-form video production package (immutable)';

create index productions_production_run_id_idx on public.productions (production_run_id);
create index productions_opportunity_id_idx on public.productions (opportunity_id);

-- --- immutability -------------------------------------------------------------------
-- public.forbid_update() already exists (created alongside syntheses/insights).

create trigger production_runs_immutable before update on public.production_runs
  for each row execute function public.forbid_update();
create trigger productions_immutable before update on public.productions
  for each row execute function public.forbid_update();

-- --- RLS: read-only for the dashboard roles, same shape as syntheses/opportunities --

alter table public.production_runs enable row level security;
alter table public.productions enable row level security;

create policy "dashboard can read production runs"
  on public.production_runs for select to anon, authenticated using (true);
create policy "dashboard can read productions"
  on public.productions for select to anon, authenticated using (true);

-- --- atomic write -------------------------------------------------------------------
--
-- payload = {
--   "run": {opportunity_id, research_job_id, product_key, product_version,
--           analysis_type, model, prompt_version, schema_version, run_key,
--           input_manifest, result},
--   "production": {title, hook, target_audience, objective, platform,
--                  estimated_duration_seconds, scenes, script, cta, caption,
--                  hashtags, product_capability_refs, limitations}
-- }
--
-- The payload has ALREADY been validated by the research service (schema,
-- references, product truth, scene/duration sanity); this function only
-- stores it atomically.
--
-- Returns {"status": "created"|"exists", "run_id": ..., "production_id": ...}.
-- ON CONFLICT (run_key) DO NOTHING makes a concurrent duplicate wait for the
-- first transaction and then report "exists" instead of erroring or duplicating.

create function public.save_production(payload jsonb) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  r            jsonb := payload -> 'run';
  p            jsonb := payload -> 'production';
  new_id       uuid;
  existing_id  uuid;
  new_prod     uuid;
begin
  if r is null or p is null then
    raise exception 'save_production: malformed payload';
  end if;

  insert into public.production_runs
    (opportunity_id, research_job_id, product_key, product_version, analysis_type, model,
     prompt_version, schema_version, run_key, input_manifest, result)
  values
    ((r ->> 'opportunity_id')::uuid, (r ->> 'research_job_id')::uuid, r ->> 'product_key',
     r ->> 'product_version', r ->> 'analysis_type', r ->> 'model', r ->> 'prompt_version',
     r ->> 'schema_version', r ->> 'run_key', r -> 'input_manifest', r -> 'result')
  on conflict (run_key) do nothing
  returning id into new_id;

  if new_id is null then
    select id into existing_id from public.production_runs where run_key = r ->> 'run_key';
    return jsonb_build_object('status', 'exists', 'run_id', existing_id);
  end if;

  insert into public.productions
    (production_run_id, opportunity_id, title, hook, target_audience, objective, platform,
     estimated_duration_seconds, scenes, script, cta, caption, hashtags,
     product_capability_refs, limitations)
  values
    (new_id, (r ->> 'opportunity_id')::uuid, p ->> 'title', p ->> 'hook',
     p ->> 'target_audience', p ->> 'objective', p ->> 'platform',
     (p ->> 'estimated_duration_seconds')::integer, coalesce(p -> 'scenes', '[]'::jsonb),
     p ->> 'script', p ->> 'cta', p ->> 'caption', coalesce(p -> 'hashtags', '[]'::jsonb),
     coalesce(p -> 'product_capability_refs', '[]'::jsonb), p ->> 'limitations')
  returning id into new_prod;

  return jsonb_build_object('status', 'created', 'run_id', new_id, 'production_id', new_prod);
end $$;

-- Only the research service (service role) may write.
revoke all on function public.save_production(jsonb) from public, anon, authenticated;
grant execute on function public.save_production(jsonb) to service_role;
