-- Complete the research_jobs -> insights -> opportunities relationship
-- chain, alongside the existing research_jobs -> content -> comments chain.
--
-- Live schema already has:
--   content.research_job_id       -> research_jobs.id
--   comments.content_id           -> content.id
--   opportunities.insight_id      -> insights.id
--
-- Missing (added here):
--   insights.research_job_id      -> research_jobs.id
--   opportunities.research_job_id -> research_jobs.id
--
-- Both new columns are nullable and are not backfilled: there is no reliable
-- way to derive which research job produced existing rows.
--
-- FK behaviour mirrors the existing FKs: ON UPDATE CASCADE, ON DELETE
-- NO ACTION (default). Constraint names follow <table>_<column>_fkey.
--
-- The existing FK columns have no supporting indexes; indexes are added for
-- the two new columns so per-job lookups and FK checks are cheap.

alter table public.insights
  add column if not exists research_job_id uuid;

alter table public.opportunities
  add column if not exists research_job_id uuid;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'insights_research_job_id_fkey'
      and conrelid = 'public.insights'::regclass
  ) then
    alter table public.insights
      add constraint insights_research_job_id_fkey
      foreign key (research_job_id) references public.research_jobs (id)
      on update cascade;
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'opportunities_research_job_id_fkey'
      and conrelid = 'public.opportunities'::regclass
  ) then
    alter table public.opportunities
      add constraint opportunities_research_job_id_fkey
      foreign key (research_job_id) references public.research_jobs (id)
      on update cascade;
  end if;
end $$;

create index if not exists insights_research_job_id_idx
  on public.insights (research_job_id);

create index if not exists opportunities_research_job_id_idx
  on public.opportunities (research_job_id);
