-- Media Generation: human-editable Media Direction, and generation cost tracking.
--
-- 1. Media Direction (`media_directions`, one row per production) is a
--    genuinely NEW kind of table in this schema: every other table here is
--    append-only/immutable (see `forbid_update()` triggers throughout), but
--    Media Direction is deliberately mutable - it exists to be edited and
--    reused from the dashboard. It sits between Production (WHAT should be
--    created, immutable) and the media provider (HOW the model renders it):
--    Media Direction is HOW A HUMAN WANTS IT VISUALLY REPRESENTED, and never
--    touches the `productions` table itself (research.media_pack folds it
--    into the generated prompt at generation time; see that module).
--
--    RLS grants the dashboard's anon/authenticated role read AND write here
--    (unlike every service-role-only analytical table): this is the first
--    genuinely user-editable data in the schema, on a par with how
--    `research_jobs` already accepts a dashboard-created insert.
--
-- 2. `media_runs.media_direction_snapshot`: the exact Media Direction field
--    values in effect when a run started - a snapshot, not a live reference,
--    because `media_directions` can be edited after the run that used it.
--    research.media folds this snapshot into the run's own run_key identity,
--    so editing direction and regenerating creates a new run rather than
--    being silently skipped as already-completed by find_completed_asset.
--
-- 3. `media_assets.cost_amount` / `cost_currency`: the provider's own
--    reported generation cost for that one asset (see
--    research.media_providers.GeneratedImage/GeneratedVideo) - carried
--    through unchanged, never computed or invented by this schema. Both
--    null together when the provider had no honest figure to report.
--    Populated on a FAILED asset too when the provider call itself
--    succeeded and money was genuinely spent before a later step (e.g.
--    storage upload) failed - so "total cost" reflects real spend, not just
--    the cost of assets that happened to end up usable.
--
-- MEDIA_SCHEMA_VERSION is deliberately NOT bumped (see research.media_schema):
-- existing image-generation runs and their idempotency are unaffected.

create table public.media_directions (
  id uuid primary key default gen_random_uuid(),
  production_id uuid not null unique references public.productions(id),
  target_subject text,
  appearance text,
  setting text,
  visual_style text,
  camera_composition text,
  lighting text,
  avoid text,
  custom_instructions text,
  additional_direction text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.media_directions is
  'One row per production: human-editable visual direction for media generation (HOW to depict it) - separate from the production spec (WHAT to depict, immutable) and the provider (HOW the model renders it). Unlike every other table in this schema, this one is mutable: edited and reused, not an immutable analytical record.';

alter table public.media_directions enable row level security;

create policy "dashboard can read media directions" on public.media_directions
  for select to anon, authenticated using (true);

create policy "dashboard can create media directions" on public.media_directions
  for insert to anon, authenticated with check (true);

create policy "dashboard can update media directions" on public.media_directions
  for update to anon, authenticated using (true) with check (true);

alter table public.media_runs
  add column if not exists media_direction_snapshot jsonb;

comment on column public.media_runs.media_direction_snapshot is
  'The Media Direction field values in effect when this run started (null if none existed). A snapshot, not a live reference to media_directions: editing direction after a run does not retroactively change what that run used. Folded into the run''s run_key identity.';

alter table public.media_assets
  add column if not exists cost_amount numeric,
  add column if not exists cost_currency text;

alter table public.media_assets
  add constraint media_assets_cost_amount_non_negative
    check (cost_amount is null or cost_amount >= 0);

alter table public.media_assets
  add constraint media_assets_cost_currency_iso4217
    check (cost_currency is null or cost_currency ~ '^[A-Z]{3}$');

alter table public.media_assets
  add constraint media_assets_cost_paired
    check ((cost_amount is null) = (cost_currency is null));

comment on column public.media_assets.cost_amount is
  'The provider''s own reported generation cost for this one asset (numeric, for aggregation) - never computed or invented by this schema. Populated on a failed asset too when the provider call itself succeeded before a later step failed.';

comment on column public.media_assets.cost_currency is
  'ISO 4217 code for cost_amount (e.g. "USD") - whatever currency the provider''s own rate is actually denominated in. Always both-or-neither with cost_amount.';

-- save_media_run / save_media_asset: same contracts as before, plus the two
-- new fields passed through when present. Re-created (not edited in place)
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
     pack_version, schema_version, run_key, scene_count, input_manifest, media_direction_snapshot)
  values
    ((payload ->> 'production_id')::uuid, payload ->> 'analysis_type', payload ->> 'image_provider',
     payload ->> 'image_model', payload ->> 'video_provider', payload ->> 'video_model',
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

revoke all on function public.save_media_run(jsonb) from public, anon, authenticated;
grant execute on function public.save_media_run(jsonb) to service_role;

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
     storage_url, mime_type, width, height, duration_seconds, cost_amount, cost_currency, error_message)
  values
    ((payload ->> 'media_run_id')::uuid, (payload ->> 'production_id')::uuid,
     (payload ->> 'scene_number')::integer, payload ->> 'asset_type', payload ->> 'status',
     payload ->> 'provider', payload ->> 'model', payload ->> 'prompt',
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

revoke all on function public.save_media_asset(jsonb) from public, anon, authenticated;
grant execute on function public.save_media_asset(jsonb) to service_role;
