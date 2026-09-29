-- Media Generation v1.
--
--   productions
--   (production_id)
--     └── media_runs      one run of the media-generation engine, for one
--         │               production and one provider/model configuration
--         │               (immutable)
--         └── media_assets    one generated asset for one scene            (immutable, append-only)
--
-- Media Generation turns a Production's scenes into real generated assets
-- through a provider abstraction (research.media_providers /
-- research.gemini). It never reinterprets the creative brief: a scene's
-- purpose/visual_direction/product_capability_refs are already decided and
-- validated by Production; this layer only executes them.
--
-- Unlike syntheses/opportunity_runs/production_runs (one all-or-nothing
-- transaction per run), a media run is a SEQUENCE of independent per-scene
-- attempts: each scene is its own provider call, so a transient failure on
-- one scene must not discard the scenes that already succeeded (V1's
-- explicit requirement that scenes be independently regeneratable). Both
-- tables stay append-only/immutable like every other analytical table here -
-- a retried scene is a NEW row, never an update to the old one - so "what is
-- the current state of scene N in run R" is its LATEST row by created_at, and
-- a failed attempt is never silently hidden or overwritten to look
-- successful.
--
-- run_key (SHA-256 of the canonical run configuration) is UNIQUE: the
-- database is the final guarantee that an identical run is never created
-- twice. media_runs.scene_count is an immutable snapshot (the production's
-- scene count when the run was first created) so "N of scene_count scenes
-- completed" can be computed without re-reading the production.
--
-- Binary media is never stored in Postgres: media_assets stores only a
-- storage_bucket/storage_path/storage_url reference (see
-- research.media_storage, which uploads to Supabase Storage). The
-- `media-assets` bucket is created here too.

-- --- storage bucket -----------------------------------------------------------------

insert into storage.buckets (id, name, public)
values ('media-assets', 'media-assets', true)
on conflict (id) do nothing;

-- --- media_runs -----------------------------------------------------------------

create table public.media_runs (
  id              uuid primary key default gen_random_uuid(),
  production_id   uuid not null
                    references public.productions (id) on update cascade on delete restrict,
  analysis_type   text not null,
  image_provider  text not null,
  image_model     text not null,
  video_provider  text,  -- null: no video asset requested/available for this run
  video_model     text,
  pack_version    text not null,
  schema_version  text not null,
  run_key         text not null,
  scene_count     integer not null,  -- immutable snapshot of the production's scene count
  input_manifest  jsonb not null,
  created_at      timestamptz not null default now(),
  constraint media_runs_run_key_key unique (run_key),
  constraint media_runs_run_key_format check (run_key ~ '^[0-9a-f]{64}$'),
  constraint media_runs_scene_count_positive check (scene_count > 0),
  -- target of the composite FK that keeps media_assets.production_id consistent
  constraint media_runs_id_production_id_key unique (id, production_id)
);

comment on table public.media_runs is
  'One row is one media-generation run over a production and provider/model configuration (immutable)';

create index media_runs_production_id_idx on public.media_runs (production_id);

-- --- media_assets -------------------------------------------------------------------

create table public.media_assets (
  id                       uuid primary key default gen_random_uuid(),
  media_run_id             uuid not null,
  production_id            uuid not null,
  scene_number             integer not null,
  asset_type               text not null,
  status                   text not null,
  provider                 text not null,
  model                    text not null,
  prompt                   text not null,
  generation_config        jsonb not null,
  product_capability_refs  jsonb not null default '[]',  -- must stay within the scene's own
  provider_metadata        jsonb not null default '{}',
  storage_bucket           text,
  storage_path             text,
  storage_url              text,
  mime_type                text,
  width                    integer,
  height                   integer,
  error_message            text,
  created_at               timestamptz not null default now(),
  constraint media_assets_scene_number_positive check (scene_number > 0),
  constraint media_assets_asset_type_check check (asset_type in ('image', 'video')),
  constraint media_assets_status_check check (status in ('completed', 'failed')),
  -- a completed asset always has a storage reference and no error; a failed
  -- one always has an error and no storage reference - never both, never neither
  constraint media_assets_completed_has_storage_no_error
    check (
      (status = 'completed' and storage_path is not null and storage_url is not null and error_message is null)
      or
      (status = 'failed' and storage_path is null and storage_url is null and error_message is not null)
    ),
  -- an asset and its run cannot point at different productions
  constraint media_assets_run_production_fkey
    foreign key (media_run_id, production_id)
    references public.media_runs (id, production_id)
    on update cascade on delete cascade
);

comment on table public.media_assets is
  'One row is one generation attempt (success or failure) for one production scene (immutable, append-only)';

create index media_assets_media_run_id_idx on public.media_assets (media_run_id);
create index media_assets_production_id_idx on public.media_assets (production_id);
create index media_assets_scene_lookup_idx on public.media_assets (media_run_id, scene_number, created_at desc);

-- --- immutability -------------------------------------------------------------------
-- public.forbid_update() already exists (created alongside syntheses/insights).

create trigger media_runs_immutable before update on public.media_runs
  for each row execute function public.forbid_update();
create trigger media_assets_immutable before update on public.media_assets
  for each row execute function public.forbid_update();

-- --- RLS: read-only for the dashboard roles, same shape as every other analytical table --

alter table public.media_runs enable row level security;
alter table public.media_assets enable row level security;

create policy "dashboard can read media runs"
  on public.media_runs for select to anon, authenticated using (true);
create policy "dashboard can read media assets"
  on public.media_assets for select to anon, authenticated using (true);

-- --- atomic writes ------------------------------------------------------------------
--
-- save_media_run: get-or-create by run_key (ON CONFLICT DO NOTHING), same
-- pattern as save_synthesis/save_opportunities/save_production.
--
-- save_media_asset: a plain, single-row atomic insert. There is deliberately
-- no ON CONFLICT / idempotency check here - idempotency is decided in Python
-- BEFORE calling the provider (research.media_store.find_completed_asset), so
-- by the time this function is called a real generation attempt has already
-- happened and its outcome (success or failure) must always be recorded.

create function public.save_media_run(payload jsonb) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  new_id      uuid;
  existing_id uuid;
begin
  insert into public.media_runs
    (production_id, analysis_type, image_provider, image_model, video_provider, video_model,
     pack_version, schema_version, run_key, scene_count, input_manifest)
  values
    ((payload ->> 'production_id')::uuid, payload ->> 'analysis_type', payload ->> 'image_provider',
     payload ->> 'image_model', payload ->> 'video_provider', payload ->> 'video_model',
     payload ->> 'pack_version', payload ->> 'schema_version', payload ->> 'run_key',
     (payload ->> 'scene_count')::integer, payload -> 'input_manifest')
  on conflict (run_key) do nothing
  returning id into new_id;

  if new_id is null then
    select id into existing_id from public.media_runs where run_key = payload ->> 'run_key';
    return jsonb_build_object('status', 'exists', 'run_id', existing_id);
  end if;

  return jsonb_build_object('status', 'created', 'run_id', new_id);
end $$;

create function public.save_media_asset(payload jsonb) returns jsonb
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
     storage_url, mime_type, width, height, error_message)
  values
    ((payload ->> 'media_run_id')::uuid, (payload ->> 'production_id')::uuid,
     (payload ->> 'scene_number')::integer, payload ->> 'asset_type', payload ->> 'status',
     payload ->> 'provider', payload ->> 'model', payload ->> 'prompt',
     coalesce(payload -> 'generation_config', '{}'::jsonb),
     coalesce(payload -> 'product_capability_refs', '[]'::jsonb),
     coalesce(payload -> 'provider_metadata', '{}'::jsonb),
     payload ->> 'storage_bucket', payload ->> 'storage_path', payload ->> 'storage_url',
     payload ->> 'mime_type', (payload ->> 'width')::integer, (payload ->> 'height')::integer,
     payload ->> 'error_message')
  returning id into new_id;

  return jsonb_build_object('status', 'created', 'asset_id', new_id);
end $$;

-- Only the research service (service role) may write.
revoke all on function public.save_media_run(jsonb) from public, anon, authenticated;
revoke all on function public.save_media_asset(jsonb) from public, anon, authenticated;
grant execute on function public.save_media_run(jsonb) to service_role;
grant execute on function public.save_media_asset(jsonb) to service_role;
