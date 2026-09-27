-- Remove the dead classification columns from public.content.
--
-- content is raw/source evidence; interpretations (20260927100000) is the
-- authoritative, append-only store of derived analysis. The research service no
-- longer writes these columns and the dashboard reads classification from
-- interpretations.result, so nothing depends on them any more.
--
-- Kept: transcript, title, description, performance fields, raw metadata.
--
-- Safety: refuse to drop unless every content row that still carries a legacy
-- value also has a classification interpretation (i.e. the value is not the
-- only copy). Raising aborts the whole migration; nothing is dropped.

do $$
begin
  if exists (
    select 1 from public.content c
    where (c.topic is not null or c.audience is not null or c.pain_point is not null
           or c.hook is not null or c.hook_type is not null or c.format is not null
           or c.emotion is not null or c.cta is not null)
      and not exists (
        select 1 from public.interpretations i
        where i.content_id = c.id and i.analysis_type = 'classification'
      )
  ) then
    raise exception
      'content has legacy classification values with no interpretation; not dropping';
  end if;
end $$;

alter table public.content
  drop column if exists topic,
  drop column if exists audience,
  drop column if exists pain_point,
  drop column if exists hook,
  drop column if exists hook_type,
  drop column if exists format,
  drop column if exists emotion,
  drop column if exists cta;
