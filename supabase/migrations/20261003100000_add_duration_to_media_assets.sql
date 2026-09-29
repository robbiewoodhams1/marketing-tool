-- Media Generation v2: add video support.
--
-- Additive only: the `media_assets` table and `save_media_asset` function
-- from the previous chapter already supported asset_type = 'video'
-- structurally (the CHECK constraint already allowed it, and the dashboard's
-- Media page already renders a <video> element for a non-image asset type).
-- Two things were genuinely missing:
--
-- 1. A place to record how long a generated video actually is - images never
--    have this, so the column is nullable and untouched for every existing
--    (image) row.
--
--    `duration_seconds` is the value the video provider was ASKED for (the
--    nearest value it documents support for, e.g. Veo only accepts 4, 6 or 8
--    seconds - see research.veo), not something measured from the file: no
--    provider response documented at implementation time reports a measured
--    duration, and this service does not probe media files itself.
--
-- 2. `media_runs.image_provider`/`image_model` were NOT NULL, which quietly
--    assumed every run is (at least also) an image run. A video-only run has
--    no image provider at all, so those two columns become nullable, with a
--    CHECK that a run is still identifiable as being for at least one real
--    modality: (image_provider and image_model) or (video_provider and
--    video_model). research.media.py now populates exactly one pair per run
--    (never both, never neither) so an image run's identity/run_key no
--    longer depends on whatever unrelated MEDIA_VIDEO_* env vars happen to be
--    set, and vice versa.
--
-- Existing image-generation runs are entirely unaffected: MEDIA_SCHEMA_VERSION
-- is deliberately NOT bumped for this change (see research.media_schema), so
-- run_key identity - and therefore idempotency for already-completed image
-- scenes - is preserved exactly as it was.

alter table public.media_assets
  add column if not exists duration_seconds numeric;

alter table public.media_assets
  add constraint media_assets_duration_positive
    check (duration_seconds is null or duration_seconds > 0);

comment on column public.media_assets.duration_seconds is
  'Video only (null for images): the duration actually requested of the provider, not measured from the file.';

alter table public.media_runs
  alter column image_provider drop not null,
  alter column image_model drop not null;

alter table public.media_runs
  add constraint media_runs_has_one_modality
    check (
      (image_provider is not null and image_model is not null)
      or (video_provider is not null and video_model is not null)
    );

-- save_media_asset: same contract as before, plus duration_seconds passed
-- through when present. Re-created (not edited in place) because Postgres
-- functions are replaced wholesale, not migrated column-by-column; the
-- historical migration that first created it is untouched.
create or replace function public.save_media_asset(payload jsonb) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  new_id uuid;
  run_production_id uuid;
begin
  select production_id into run_production_id
  from public.media_runs where id = (payload ->> 'media_run_id')::uuid;

  if run_production_id is null then
    raise exception 'save_media_asset: media_run % does not exist', payload ->> 'media_run_id';
  end if;
  if run_production_id is distinct from (payload ->> 'production_id')::uuid then
    raise exception 'save_media_asset: production_id does not match the media run''s own';
  end if;

  insert into public.media_assets
    (media_run_id, production_id, scene_number, asset_type, status, provider, model, prompt,
     generation_config, product_capability_refs, provider_metadata, storage_bucket, storage_path,
     storage_url, mime_type, width, height, duration_seconds, error_message)
  values
    ((payload ->> 'media_run_id')::uuid, (payload ->> 'production_id')::uuid,
     (payload ->> 'scene_number')::integer, payload ->> 'asset_type', payload ->> 'status',
     payload ->> 'provider', payload ->> 'model', payload ->> 'prompt',
     coalesce(payload -> 'generation_config', '{}'::jsonb),
     coalesce(payload -> 'product_capability_refs', '[]'::jsonb),
     coalesce(payload -> 'provider_metadata', '{}'::jsonb),
     payload ->> 'storage_bucket', payload ->> 'storage_path', payload ->> 'storage_url',
     payload ->> 'mime_type', (payload ->> 'width')::integer, (payload ->> 'height')::integer,
     (payload ->> 'duration_seconds')::numeric, payload ->> 'error_message')
  returning id into new_id;

  return jsonb_build_object('status', 'created', 'asset_id', new_id);
end $$;

revoke all on function public.save_media_asset(jsonb) from public, anon, authenticated;
grant execute on function public.save_media_asset(jsonb) to service_role;
