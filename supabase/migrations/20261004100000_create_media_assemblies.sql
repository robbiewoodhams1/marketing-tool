-- Media Assembly v1.
--
--   productions
--   (production_id)
--     └── media_assemblies    one attempt to combine a production's
--                              completed scene VIDEO assets into a single
--                              finished MP4 (immutable, append-only)
--
-- Assembly is a separate, mechanical stage, downstream of Media Generation:
--
--   Production        -> WHAT should exist (the scenes and their order)
--   Media Generation   -> individual generated assets, one provider call each
--   Assembly (here)    -> combines already-generated assets into one
--                         finished file
--
-- Unlike media_runs/media_assets (a SEQUENCE of independent per-scene
-- writes, because partial success is meaningful there), one assembly
-- attempt is a single all-or-nothing outcome - like syntheses/
-- opportunity_runs/production_runs - so this is ONE table, not a run/asset
-- pair: either a finished MP4 exists or the attempt failed, recorded as one
-- immutable row either way (never updated - a retry is a new row).
--
-- source_asset_ids is the EXACT ORDERED set of media_assets.id this attempt
-- combined (one per scene, in scene order) - not a foreign key to a single
-- media_run, because Assembly may combine video assets that came from
-- different media runs (e.g. scene 3 was regenerated later after a Media
-- Direction edit, in a new run, while scenes 1-2-4-5-6 came from the
-- original one). run_key is a SHA-256 of (production_id, source_asset_ids).
-- If any scene's underlying asset is later regenerated, its id changes,
-- source_asset_ids changes, and so does run_key - so a re-assembly after a
-- regeneration is correctly a NEW row, never silently skipped.
--
-- Unlike media_runs (unique on run_key across every row, because a run's
-- identity is a fixed configuration regardless of how its scenes turn out),
-- one assembly ROW represents one whole ATTEMPT, and a failed attempt must
-- remain retryable - so the uniqueness guarantee is a PARTIAL unique index,
-- over completed rows only: at most one completed assembly per run_key, but
-- any number of failed ones (each a kept, immutable record of that attempt,
-- exactly like a failed media_assets row). Application code
-- (research.assembly_store.AssemblyRepository.find_assembly) only ever
-- treats a COMPLETED row as "already done"; this index is the final,
-- database-level backstop for that same rule.
--
-- source_duration_seconds is the sum of the source assets' own
-- duration_seconds (what Media Generation actually got from Veo - see
-- media_assets.duration_seconds's own comment) - null if any source asset's
-- duration is unknown, never a silently-partial sum.
-- output_duration_seconds is independently measured (ffprobe) from the
-- actual assembled file - never assumed to equal source_duration_seconds,
-- since normalisation/re-encoding can shift exact timing slightly.
--
-- Binary media is never stored in Postgres, same as media_assets: only a
-- storage_bucket/storage_path/storage_url reference to the same
-- `media-assets` bucket Media Generation already uses (see
-- research.media_storage.assembly_output_path).
--
-- Assembly makes no AI provider call of its own, so there is deliberately no
-- cost_amount/cost_currency here (see research.assembly's module
-- docstring) - inventing an FFmpeg-processing price would be exactly the
-- kind of fabricated cost figure this project has consistently avoided for
-- every real provider cost (see media_assets.cost_amount's own comment).
-- The dashboard shows Assembly's contribution as £0/no external cost
-- alongside the real media-generation costs it combines, rather than
-- pretending Assembly has a provider cost.

create table public.media_assemblies (
  id                        uuid primary key default gen_random_uuid(),
  production_id             uuid not null
                              references public.productions (id) on update cascade on delete restrict,
  run_key                   text not null,
  status                    text not null,
  source_asset_ids          uuid[] not null,
  source_scene_count        integer not null,
  source_duration_seconds   numeric,
  output_duration_seconds   numeric,
  output_storage_bucket     text,
  output_storage_path       text,
  output_storage_url        text,
  output_mime_type          text,
  error_message             text,
  created_at                timestamptz not null default now(),
  completed_at              timestamptz,  -- set only when status = 'completed'; null for a failed attempt
  constraint media_assemblies_run_key_format check (run_key ~ '^[0-9a-f]{64}$'),
  constraint media_assemblies_status_check check (status in ('completed', 'failed')),
  constraint media_assemblies_source_scene_count_positive check (source_scene_count > 0),
  constraint media_assemblies_source_asset_ids_not_empty check (array_length(source_asset_ids, 1) > 0),
  constraint media_assemblies_source_duration_non_negative
    check (source_duration_seconds is null or source_duration_seconds >= 0),
  constraint media_assemblies_output_duration_non_negative
    check (output_duration_seconds is null or output_duration_seconds >= 0),
  -- a completed assembly always has an output reference, a completed_at,
  -- and no error; a failed one always has an error and no output
  -- reference/completed_at - never both, never neither
  constraint media_assemblies_completed_has_output_no_error
    check (
      (status = 'completed' and output_storage_path is not null and output_storage_url is not null
       and output_duration_seconds is not null and completed_at is not null and error_message is null)
      or
      (status = 'failed' and output_storage_path is null and output_storage_url is null
       and completed_at is null and error_message is not null)
    )
);

comment on table public.media_assemblies is
  'One row is one attempt to combine a production''s completed scene video assets into a single finished MP4 (immutable, append-only)';

create index media_assemblies_production_id_idx on public.media_assemblies (production_id);
create index media_assemblies_source_asset_ids_idx on public.media_assemblies using gin (source_asset_ids);

-- The idempotency guarantee itself: at most one COMPLETED assembly per
-- run_key (see the table comment above for why this is partial, not a
-- plain unique constraint).
create unique index media_assemblies_completed_run_key_key
  on public.media_assemblies (run_key) where status = 'completed';

-- --- immutability -------------------------------------------------------------------
-- public.forbid_update() already exists (created alongside syntheses/insights/media_runs).

create trigger media_assemblies_immutable before update on public.media_assemblies
  for each row execute function public.forbid_update();

-- --- RLS: read-only for the dashboard roles, same shape as every other analytical table --

alter table public.media_assemblies enable row level security;

create policy "dashboard can read media assemblies"
  on public.media_assemblies for select to anon, authenticated using (true);

-- --- atomic write --------------------------------------------------------------------
--
-- A plain, single-row atomic insert - no ON CONFLICT / idempotency check
-- here, mirroring save_media_asset: idempotency is decided in Python BEFORE
-- assembling (research.assembly_store.AssemblyRepository.find_assembly), so
-- by the time this function is called a real assembly attempt has already
-- happened and its outcome (success or failure) must always be recorded.
-- The partial unique index on completed run_keys remains the final
-- database-level guard.

create function public.save_media_assembly(payload jsonb) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  new_id uuid;
begin
  insert into public.media_assemblies
    (production_id, run_key, status, source_asset_ids, source_scene_count, source_duration_seconds,
     output_duration_seconds, output_storage_bucket, output_storage_path, output_storage_url,
     output_mime_type, error_message, completed_at)
  values
    ((payload ->> 'production_id')::uuid, payload ->> 'run_key', payload ->> 'status',
     (select array_agg(elem::uuid) from jsonb_array_elements_text(payload -> 'source_asset_ids') as elem),
     (payload ->> 'source_scene_count')::integer, (payload ->> 'source_duration_seconds')::numeric,
     (payload ->> 'output_duration_seconds')::numeric, payload ->> 'output_storage_bucket',
     payload ->> 'output_storage_path', payload ->> 'output_storage_url', payload ->> 'output_mime_type',
     payload ->> 'error_message',
     case when payload ->> 'status' = 'completed' then now() else null end)
  returning id into new_id;

  return jsonb_build_object('status', 'created', 'assembly_id', new_id);
end $$;

revoke all on function public.save_media_assembly(jsonb) from public, anon, authenticated;
grant execute on function public.save_media_assembly(jsonb) to service_role;
