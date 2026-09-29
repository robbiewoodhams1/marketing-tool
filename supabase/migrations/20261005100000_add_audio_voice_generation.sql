-- Audio / Voice Generation v1.
--
-- Audio is its own media layer, not a Media Generation afterthought (see
-- research.voice's own module docstring for the full architectural note).
-- Eventually: visuals + scene/ambient audio + voiceover + music + sound
-- effects -> audio mixing -> final video. This chapter builds ONLY
-- voiceover generation - one new `asset_type` value on the EXISTING
-- media_assets table, reusing every column that already applies (duration,
-- provider, model, cost, storage reference, status), plus one new nullable
-- column (`audio_subtype`) distinguishing voiceover from future ambient/
-- music/SFX audio. No existing image/video row or constraint is affected:
-- both ALTER CONSTRAINT rewrites below strictly widen the allowed value set
-- and cannot invalidate anything already stored.
--
-- media_runs gains a third provider/model pair (voice_provider/voice_model),
-- exactly mirroring how video_provider/video_model were added to the
-- originally image-only media_runs table - a run is for exactly one media
-- type (image, video, or now voice), never more than one, so the
-- "has one modality" CHECK becomes "has exactly one of three pairs" rather
-- than two.
--
-- Explicitly NOT built here: music/SFX generation, full audio mixing,
-- captions/subtitles, and - critically - the source Veo clips' own
-- generated scene audio is neither deleted nor touched: Assembly V1
-- (see the 20261004100000 migration) deliberately produced a MUTE final
-- video by concatenating only the visual streams, and that source audio
-- remains present in the original, unmodified Veo scene video files in
-- storage. A future Assembly/audio-mixing chapter can choose to preserve,
-- mute, mix, or replace it - nothing about that choice is made here.

alter table public.media_assets
  drop constraint media_assets_asset_type_check;
alter table public.media_assets
  add constraint media_assets_asset_type_check check (asset_type in ('image', 'video', 'audio'));

alter table public.media_assets
  add column if not exists audio_subtype text;

alter table public.media_assets
  add constraint media_assets_audio_subtype_known_value
    check (audio_subtype is null or audio_subtype in ('voiceover', 'ambient', 'music', 'sfx'));
alter table public.media_assets
  add constraint media_assets_audio_subtype_only_for_audio
    check (asset_type = 'audio' or audio_subtype is null);

comment on column public.media_assets.audio_subtype is
  'Audio only (null for image/video): which kind of audio this is. V1 only ever writes ''voiceover''; ambient/music/sfx are reserved for future chapters.';

alter table public.media_runs
  add column if not exists voice_provider text,
  add column if not exists voice_model text;

alter table public.media_runs
  drop constraint media_runs_has_one_modality;
alter table public.media_runs
  add constraint media_runs_has_one_modality
    check (
      (image_provider is not null and image_model is not null)
      or (video_provider is not null and video_model is not null)
      or (voice_provider is not null and voice_model is not null)
    );

-- save_media_run / save_media_asset: same contracts as before, plus the new
-- fields passed through when present. Re-created (not edited in place)
-- because Postgres functions are replaced wholesale, not migrated
-- column-by-column; the historical migrations that first created them are
-- untouched.
create or replace function public.save_media_run(payload jsonb) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  new_id      uuid;
  existing_id uuid;
begin
  insert into public.media_runs
    (production_id, analysis_type, image_provider, image_model, video_provider, video_model,
     voice_provider, voice_model, pack_version, schema_version, run_key, scene_count, input_manifest,
     media_direction_snapshot)
  values
    ((payload ->> 'production_id')::uuid, payload ->> 'analysis_type', payload ->> 'image_provider',
     payload ->> 'image_model', payload ->> 'video_provider', payload ->> 'video_model',
     payload ->> 'voice_provider', payload ->> 'voice_model',
     payload ->> 'pack_version', payload ->> 'schema_version', payload ->> 'run_key',
     (payload ->> 'scene_count')::integer, payload -> 'input_manifest', payload -> 'media_direction_snapshot')
  on conflict (run_key) do nothing
  returning id into new_id;

  if new_id is null then
    select id into existing_id from public.media_runs where run_key = payload ->> 'run_key';
    return jsonb_build_object('status', 'exists', 'run_id', existing_id);
  end if;

  return jsonb_build_object('status', 'created', 'run_id', new_id);
end $$;

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
    (media_run_id, production_id, scene_number, asset_type, audio_subtype, status, provider, model, prompt,
     generation_config, product_capability_refs, provider_metadata, storage_bucket, storage_path,
     storage_url, mime_type, width, height, duration_seconds, cost_amount, cost_currency, error_message)
  values
    ((payload ->> 'media_run_id')::uuid, (payload ->> 'production_id')::uuid,
     (payload ->> 'scene_number')::integer, payload ->> 'asset_type', payload ->> 'audio_subtype',
     payload ->> 'status', payload ->> 'provider', payload ->> 'model', payload ->> 'prompt',
     coalesce(payload -> 'generation_config', '{}'::jsonb),
     coalesce(payload -> 'product_capability_refs', '[]'::jsonb),
     coalesce(payload -> 'provider_metadata', '{}'::jsonb),
     payload ->> 'storage_bucket', payload ->> 'storage_path', payload ->> 'storage_url',
     payload ->> 'mime_type', (payload ->> 'width')::integer, (payload ->> 'height')::integer,
     (payload ->> 'duration_seconds')::numeric, (payload ->> 'cost_amount')::numeric,
     payload ->> 'cost_currency', payload ->> 'error_message')
  returning id into new_id;

  return jsonb_build_object('status', 'created', 'asset_id', new_id);
end $$;

revoke all on function public.save_media_run(jsonb) from public, anon, authenticated;
revoke all on function public.save_media_asset(jsonb) from public, anon, authenticated;
grant execute on function public.save_media_run(jsonb) to service_role;
grant execute on function public.save_media_asset(jsonb) to service_role;
