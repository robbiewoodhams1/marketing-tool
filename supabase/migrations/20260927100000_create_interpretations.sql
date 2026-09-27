-- Store AI interpretations as separate, append-only records.
--
-- content is raw research evidence. An interpretation is derived analysis and
-- must not be a 1:1 mutable property of a content row: several models, prompt
-- versions and schema versions may analyse the same content, re-running never
-- overwrites earlier results, and analysis types other than classification can
-- be added later without schema changes.
--
-- There is deliberately NO unique constraint: the same content can hold
--   classification / model A / prompt v1
--   classification / model B / prompt v1
--   classification / model A / prompt v2
-- at once. Avoiding accidental duplicates on a retry is the application's job
-- (research.persistence.save_classifications checks the exact
-- content + analysis_type + model + prompt_version + schema_version first).
--
-- Deleting a content row deletes its interpretations (ON DELETE CASCADE): they
-- are derived from that content and meaningless without it.

create table if not exists public.interpretations (
  id             uuid primary key default gen_random_uuid(),
  content_id     uuid not null
                   references public.content (id) on update cascade on delete cascade,
  analysis_type  text not null,
  model          text not null,
  prompt_version text not null,
  schema_version text not null,
  result         jsonb not null,
  created_at     timestamptz not null default now()
);

comment on table public.interpretations is
  'One row is one AI interpretation of one content row (append-only)';

create index if not exists interpretations_content_id_idx
  on public.interpretations (content_id);

-- Same posture as content/comments: RLS on, no policies; the research service
-- uses the service-role key, which bypasses RLS.
alter table public.interpretations enable row level security;

-- Preserve the one live smoke-test classification (previously stored in
-- content.classification) before that column is dropped.
insert into public.interpretations
  (content_id, analysis_type, model, prompt_version, schema_version, result)
select c.id, 'classification', 'claude-haiku-4-5-20251001',
       'classification-v2', 'classification-v1', c.classification
from public.content c
where c.id = '2d92a734-5efe-43fc-a58f-53c33d23acb6'
  and c.classification is not null
  and not exists (
    select 1 from public.interpretations i
    where i.content_id = c.id and i.analysis_type = 'classification'
      and i.model = 'claude-haiku-4-5-20251001'
      and i.prompt_version = 'classification-v2'
      and i.schema_version = 'classification-v1'
  );

-- Refuse to drop the column unless EVERY stored classification now exists,
-- identical, in interpretations and has all eight fields. Raising aborts the
-- whole migration (nothing is dropped, nothing is committed).
do $$
begin
  if exists (
    select 1 from public.content c
    where c.classification is not null
      and (
        not exists (
          select 1 from public.interpretations i
          where i.content_id = c.id and i.analysis_type = 'classification'
            and i.result = c.classification
        )
        or not (c.classification ?& array[
          'topic','audience','pain_point','hook','hook_type','format','emotion','cta'])
      )
  ) then
    raise exception
      'content.classification holds data not preserved in interpretations; not dropping';
  end if;
end $$;

alter table public.content drop column if exists classification;

-- content.topic, audience, pain_point, hook, hook_type, format, emotion and cta
-- are kept but are NOT authoritative and are no longer written by the research
-- service. The authoritative result is interpretations.result.
