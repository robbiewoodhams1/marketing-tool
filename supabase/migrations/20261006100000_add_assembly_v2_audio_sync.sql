-- Media Assembly V2: voiceover-synchronised assembly.
--
-- Assembly V1 (20261004100000) combined only completed scene VIDEO assets,
-- muting any source audio. Now that voiceover exists as its own generated
-- asset (20261005100000, research.voice), Assembly V2
-- (research.assembly_v2) synchronises each scene's video against its own
-- voiceover clip before concatenating - see research.ffmpeg_engine's module
-- docstring for the full deterministic timing/sync policy. This extends the
-- EXISTING media_assemblies table rather than introducing a parallel
-- concept: one assembly attempt is still one immutable row, success or
-- failure, whichever policy produced it.
--
-- New columns:
--
--   source_voice_asset_ids  the EXACT ORDERED set of media_assets.id (one
--                           per scene, in scene order) this attempt
--                           synchronised as narration - mirrors
--                           source_asset_ids (video), which this migration
--                           does NOT rename: a V1 row's source_asset_ids
--                           remain exactly what they always were. Null for a
--                           V1, picture-only assembly; non-null (and always
--                           the same length as source_scene_count) for a V2,
--                           voiceover-synchronised one.
--
--   audio_policy            which audio-assembly policy produced this row -
--                           'mute-v1' (Assembly V1: no audio track at all)
--                           or 'voiceover-sync-v1' (Assembly V2). Folded
--                           into research.assembly_store.compute_assembly_run_key
--                           precisely so a future audio-mixing change (e.g.
--                           a policy that also preserves source Veo audio)
--                           can never be mistaken, by run_key alone, for a
--                           result produced under today's policy - even
--                           given the exact same source assets. Existing V1
--                           rows are backfilled 'mute-v1', which is exactly
--                           what they always were.
--
--   scene_manifest          Assembly V2 only (null for V1); one JSON object
--                           per scene, in scene order: scene_number,
--                           video_asset_id, voice_asset_id,
--                           video_duration_seconds, voice_duration_seconds,
--                           scene_duration_seconds (the actual,
--                           ffprobe-measured numbers the scene was
--                           synchronised to - never the production's
--                           originally requested scene duration), and
--                           sync_strategy (which of the three deterministic
--                           strategies applied - see
--                           research.ffmpeg_engine.sync_strategy_for). Kept
--                           as a single flexible jsonb column, not its own
--                           table/columns, so a future audio layer
--                           (ambient/music/SFX/ducking) can extend it
--                           without another migration.
--
-- Idempotency: research.assembly_store.compute_assembly_run_key now hashes
-- (production_id, source_asset_ids, source_voice_asset_ids, audio_policy) -
-- so changing EITHER a scene's video OR its voiceover (or the policy itself)
-- changes the run_key, and therefore always produces a genuinely new
-- assembly rather than silently reusing a stale one. The partial unique
-- index on completed run_keys (created in 20261004100000) needs no change:
-- it already guards uniqueness by run_key alone, whatever went into it.

alter table public.media_assemblies
  add column if not exists source_voice_asset_ids uuid[],
  add column if not exists audio_policy text not null default 'mute-v1',
  add column if not exists scene_manifest jsonb;

alter table public.media_assemblies
  add constraint media_assemblies_audio_policy_known_value
    check (audio_policy in ('mute-v1', 'voiceover-sync-v1'));

-- A V2 (voiceover) assembly must have exactly one voice asset id per scene;
-- a V1 (mute) assembly has none. Never a partial/mismatched pairing.
alter table public.media_assemblies
  add constraint media_assemblies_source_voice_asset_ids_paired_with_scenes
    check (source_voice_asset_ids is null or array_length(source_voice_asset_ids, 1) = source_scene_count);

comment on column public.media_assemblies.source_voice_asset_ids is
  'Assembly V2 only (null for a V1, picture-only assembly): the ordered source voiceover media_assets.id, one per scene.';
comment on column public.media_assemblies.audio_policy is
  'Which audio-assembly policy produced this row: mute-v1 (V1, no audio) or voiceover-sync-v1 (V2, voiceover is the sole spoken audio).';
comment on column public.media_assemblies.scene_manifest is
  'Assembly V2 only (null or an empty array for V1): one JSON object per scene with its actual measured video/voice/scene durations and which sync strategy applied.';

create index media_assemblies_source_voice_asset_ids_idx
  on public.media_assemblies using gin (source_voice_asset_ids);

-- save_media_assembly: same contract as before, plus the three new fields
-- passed through when present (audio_policy defaults to 'mute-v1' if the
-- caller omits it, matching every existing research.assembly (V1) call
-- site - it never needed to know this field exists).
create or replace function public.save_media_assembly(payload jsonb) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  new_id uuid;
begin
  insert into public.media_assemblies
    (production_id, run_key, status, source_asset_ids, source_scene_count, source_duration_seconds,
     output_duration_seconds, output_storage_bucket, output_storage_path, output_storage_url,
     output_mime_type, error_message, source_voice_asset_ids, audio_policy, scene_manifest, completed_at)
  values
    ((payload ->> 'production_id')::uuid, payload ->> 'run_key', payload ->> 'status',
     (select array_agg(elem::uuid) from jsonb_array_elements_text(payload -> 'source_asset_ids') as elem),
     (payload ->> 'source_scene_count')::integer, (payload ->> 'source_duration_seconds')::numeric,
     (payload ->> 'output_duration_seconds')::numeric, payload ->> 'output_storage_bucket',
     payload ->> 'output_storage_path', payload ->> 'output_storage_url', payload ->> 'output_mime_type',
     payload ->> 'error_message',
     (select array_agg(elem::uuid)
        from jsonb_array_elements_text(coalesce(payload -> 'source_voice_asset_ids', '[]'::jsonb)) as elem),
     coalesce(payload ->> 'audio_policy', 'mute-v1'), payload -> 'scene_manifest',
     case when payload ->> 'status' = 'completed' then now() else null end)
  returning id into new_id;

  return jsonb_build_object('status', 'created', 'assembly_id', new_id);
end $$;

revoke all on function public.save_media_assembly(jsonb) from public, anon, authenticated;
grant execute on function public.save_media_assembly(jsonb) to service_role;
