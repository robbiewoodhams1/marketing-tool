-- Research Synthesis v1.
--
--   research_job
--     └── syntheses          an analytical run over a job's evidence (immutable)
--           └── insights     findings produced by that run          (immutable)
--                 └── insight_evidence   real FKs to the supporting evidence
--
-- content = raw evidence, interpretations = derived analysis of ONE content
-- item, syntheses/insights = derived analysis of the WHOLE dataset. Raw
-- evidence and earlier analytical results are never modified.
--
-- syntheses is the authoritative record that "this analytical process was run
-- against this exact evidence set with this exact configuration". It exists
-- even when the run found no insights (result.no_insights_reason).
--
-- run_key (SHA-256 of the canonical run configuration, built by the research
-- service) is UNIQUE: the database is the final guarantee that an identical run
-- is never stored twice, even under concurrent execution. A changed model,
-- prompt version, schema version, pack version, evidence selection or bound
-- yields a different run_key and therefore a new, additive run.
--
-- Writes go through save_synthesis(), which stores a fully validated payload
-- in ONE transaction (all or nothing). The tables are read-only to the
-- dashboard roles.

-- insights/opportunities are still empty, so reshaping insights needs no
-- backfill. Refuse to run if that ever stops being true.
do $$
begin
  if exists (select 1 from public.insights)
     or exists (select 1 from public.opportunities where insight_id is not null) then
    raise exception 'insights/opportunities contain data; refusing to reshape insights';
  end if;
end $$;

-- --- syntheses ----------------------------------------------------------------

create table public.syntheses (
  id              uuid primary key default gen_random_uuid(),
  research_job_id uuid not null
                    references public.research_jobs (id) on update cascade on delete restrict,
  analysis_type   text not null,
  model           text not null,
  prompt_version  text not null,
  schema_version  text not null,
  run_key         text not null,
  input_manifest  jsonb not null,
  result          jsonb not null,
  created_at      timestamptz not null default now(),
  constraint syntheses_run_key_key unique (run_key),
  constraint syntheses_run_key_format check (run_key ~ '^[0-9a-f]{64}$'),
  -- target of the composite FK that keeps insight.research_job_id consistent
  constraint syntheses_id_research_job_id_key unique (id, research_job_id)
);

comment on table public.syntheses is
  'One row is one analytical synthesis run over a research job (immutable)';

create index syntheses_research_job_id_created_at_idx
  on public.syntheses (research_job_id, created_at desc);

-- --- insights (empty; reshaped) -----------------------------------------------

alter table public.insights rename column description to statement;
alter table public.insights rename column confidence to model_confidence;
alter table public.insights drop column evidence_summary;  -- vague free text; see insight_evidence
alter table public.insights drop column updated_at;        -- insights are immutable

alter table public.insights
  add column synthesis_id          uuid  not null,
  add column pattern_type          text  not null,
  add column explanation           text  not null,
  add column confidence_rationale  text  not null,
  add column limitations           text  not null,
  add column observations          jsonb not null,  -- [{"text": ...}], evidence rows link by observation_index
  add column support               jsonb not null default '{}'::jsonb;  -- code-computed counts

alter table public.insights
  alter column title            set not null,
  alter column statement        set not null,
  alter column model_confidence set not null,
  alter column research_job_id  set not null;

alter table public.insights
  add constraint insights_model_confidence_range
    check (model_confidence >= 0 and model_confidence <= 1),
  -- an insight and its synthesis cannot point at different research jobs
  add constraint insights_synthesis_job_fkey
    foreign key (synthesis_id, research_job_id)
    references public.syntheses (id, research_job_id)
    on update cascade on delete cascade;

comment on table public.insights is
  'One row is one finding produced by a synthesis run (immutable)';

create index insights_synthesis_id_idx on public.insights (synthesis_id);

-- --- insight_evidence ---------------------------------------------------------

create table public.insight_evidence (
  id                uuid primary key default gen_random_uuid(),
  insight_id        uuid not null
                      references public.insights (id) on update cascade on delete cascade,
  role              text not null,
  observation_index integer,
  content_id        uuid references public.content (id) on update cascade on delete restrict,
  comment_id        uuid references public.comments (id) on update cascade on delete restrict,
  interpretation_id uuid references public.interpretations (id) on update cascade on delete restrict,
  field             text not null,
  quote             text not null,
  created_at        timestamptz not null default now(),
  constraint insight_evidence_role_check check (role in ('supports', 'counter')),
  -- exactly one evidence target
  constraint insight_evidence_one_target
    check (num_nonnulls(content_id, comment_id, interpretation_id) = 1),
  -- supporting evidence belongs to an observation; counter-evidence does not
  constraint insight_evidence_role_observation
    check ((role = 'supports') = (observation_index is not null)),
  constraint insight_evidence_observation_index_check
    check (observation_index is null or observation_index >= 0),
  constraint insight_evidence_quote_not_blank check (btrim(quote) <> '')
);

comment on table public.insight_evidence is
  'One row is one verified piece of evidence cited by an insight (immutable)';

-- Evidence sources may not be deleted while a finding cites them (ON DELETE
-- RESTRICT above); a deleted insight/synthesis takes its evidence rows with it.
create index insight_evidence_insight_id_idx on public.insight_evidence (insight_id);
create index insight_evidence_content_id_idx on public.insight_evidence (content_id);
create index insight_evidence_comment_id_idx on public.insight_evidence (comment_id);
create index insight_evidence_interpretation_id_idx on public.insight_evidence (interpretation_id);

-- --- immutability ---------------------------------------------------------------

create function public.forbid_update() returns trigger
language plpgsql as $$
begin
  raise exception '% rows are immutable (append-only)', tg_table_name;
end $$;

create trigger syntheses_immutable before update on public.syntheses
  for each row execute function public.forbid_update();
create trigger insights_immutable before update on public.insights
  for each row execute function public.forbid_update();
create trigger insight_evidence_immutable before update on public.insight_evidence
  for each row execute function public.forbid_update();

-- --- RLS: read-only for the dashboard roles, same shape as the existing policies ---

alter table public.syntheses enable row level security;
alter table public.insight_evidence enable row level security;
-- insights already has RLS enabled and "dashboard can read insights".

create policy "dashboard can read syntheses"
  on public.syntheses for select to anon, authenticated using (true);
create policy "dashboard can read insight evidence"
  on public.insight_evidence for select to anon, authenticated using (true);

-- --- atomic write ---------------------------------------------------------------
--
-- payload = {
--   "synthesis": {research_job_id, analysis_type, model, prompt_version,
--                 schema_version, run_key, input_manifest, result},
--   "insights": [{title, statement, pattern_type, model_confidence,
--                 confidence_rationale, explanation, limitations, observations,
--                 support, evidence: [{role, observation_index, content_id,
--                 comment_id, interpretation_id, field, quote}]}]
-- }
--
-- The payload has ALREADY been validated by the research service (schema,
-- references, verbatim quotes, evidence rules); this function only stores it
-- atomically. Its one check is a referential-integrity backstop: cited
-- evidence must belong to the synthesis's research job.
--
-- Returns {"status": "created"|"exists", "synthesis_id": ..., "insights": n}.
-- ON CONFLICT (run_key) DO NOTHING makes a concurrent duplicate wait for the
-- first transaction and then report "exists" instead of erroring or duplicating.

create function public.save_synthesis(payload jsonb) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  s            jsonb := payload -> 'synthesis';
  new_id       uuid;
  existing_id  uuid;
  ins          jsonb;
  ev           jsonb;
  new_insight  uuid;
  n            integer := 0;
begin
  if s is null or jsonb_typeof(payload -> 'insights') is distinct from 'array' then
    raise exception 'save_synthesis: malformed payload';
  end if;

  insert into public.syntheses
    (research_job_id, analysis_type, model, prompt_version, schema_version,
     run_key, input_manifest, result)
  values
    ((s ->> 'research_job_id')::uuid, s ->> 'analysis_type', s ->> 'model',
     s ->> 'prompt_version', s ->> 'schema_version', s ->> 'run_key',
     s -> 'input_manifest', s -> 'result')
  on conflict (run_key) do nothing
  returning id into new_id;

  if new_id is null then
    select id into existing_id from public.syntheses where run_key = s ->> 'run_key';
    return jsonb_build_object('status', 'exists', 'synthesis_id', existing_id);
  end if;

  for ins in select * from jsonb_array_elements(payload -> 'insights') loop
    insert into public.insights
      (research_job_id, synthesis_id, title, statement, pattern_type, model_confidence,
       confidence_rationale, explanation, limitations, observations, support)
    values
      ((s ->> 'research_job_id')::uuid, new_id, ins ->> 'title', ins ->> 'statement',
       ins ->> 'pattern_type', (ins ->> 'model_confidence')::numeric,
       ins ->> 'confidence_rationale', ins ->> 'explanation', ins ->> 'limitations',
       ins -> 'observations', coalesce(ins -> 'support', '{}'::jsonb))
    returning id into new_insight;
    n := n + 1;

    for ev in select * from jsonb_array_elements(coalesce(ins -> 'evidence', '[]'::jsonb)) loop
      insert into public.insight_evidence
        (insight_id, role, observation_index, content_id, comment_id,
         interpretation_id, field, quote)
      values
        (new_insight, ev ->> 'role', (ev ->> 'observation_index')::integer,
         (ev ->> 'content_id')::uuid, (ev ->> 'comment_id')::uuid,
         (ev ->> 'interpretation_id')::uuid, ev ->> 'field', ev ->> 'quote');
    end loop;
  end loop;

  if exists (
    select 1
    from public.insight_evidence e
    join public.insights i on i.id = e.insight_id
    left join public.comments cm on cm.id = e.comment_id
    left join public.interpretations ip on ip.id = e.interpretation_id
    join public.content c on c.id = coalesce(e.content_id, cm.content_id, ip.content_id)
    where i.synthesis_id = new_id and c.research_job_id is distinct from i.research_job_id
  ) then
    raise exception 'save_synthesis: cited evidence belongs to a different research job';
  end if;

  return jsonb_build_object('status', 'created', 'synthesis_id', new_id, 'insights', n);
end $$;

-- Only the research service (service role) may write.
revoke all on function public.save_synthesis(jsonb) from public, anon, authenticated;
grant execute on function public.save_synthesis(jsonb) to service_role;
