-- Captions V1: burn deterministic captions (from Production's own scene
-- `voiceover` text, never an LLM rewrite, never ASR) into an Assembly V2
-- output.
--
-- Architectural principle:
--
--   Production determines the authoritative words (scenes[].voiceover).
--   Voice Generation produces the audio (research.voice).
--   Captions represent those authoritative words visually (this chapter).
--   Assembly combines everything into the final video (research.assembly_v2).
--
-- Captions are NOT their own conventional media asset (no new
-- `media_assets.asset_type` value): a caption is not an independent
-- generated artifact with its own provider/cost - it is a deterministic,
-- reproducible DERIVATION of data that already exists (Production's
-- immutable scene text + Assembly's own ffprobe-measured scene durations),
-- computed at burn-in time. So, exactly like Assembly V2's own
-- `scene_manifest` (20261006100000), this extends the EXISTING
-- media_assemblies table with two more columns rather than inventing a
-- parallel "captions" table or asset type - "the smallest architecture that
-- fits". The persisted `caption_manifest` is what makes the caption data
-- "reproducibly derivable" inspectable/testable/reusable without re-running
-- Assembly or re-measuring anything.
--
-- New columns:
--
--   caption_policy    which caption policy produced this row -
--                      'captions-burned-v1', or NULL if captions were not
--                      requested for this assembly (captions are optional,
--                      unlike audio_policy which every assembly has some
--                      value for). Folded into
--                      research.assembly_store.compute_assembly_run_key
--                      ONLY when non-null, so an assembly produced without
--                      requesting captions hashes identically to one
--                      produced before this migration existed - existing V1/
--                      V2 assemblies remain valid and reusable. A FUTURE
--                      different caption style/timing/burn-in technique gets
--                      its own identifier, so it can never be mistaken for
--                      today's policy even given identical source assets.
--
--   caption_manifest  one JSON array entry per caption segment, on the
--                      FINAL video's own timeline (post scene-concatenation,
--                      already accounting for Assembly V2's own audio-sync
--                      scene-duration extensions - see that migration's own
--                      notes): { index, scene_number, text, start_seconds,
--                      end_seconds, timing_algorithm }. NULL iff
--                      caption_policy is NULL, for a COMPLETED row (enforced
--                      below) - a completed assembly's caption manifest
--                      never exists without a policy naming what produced
--                      it. A FAILED row may have caption_policy set with no
--                      manifest (captions were requested but the attempt
--                      never got far enough to produce one) - the same
--                      "preserve what was attempted" principle already
--                      applied to a failed row's audio_policy/
--                      source_voice_asset_ids.
--
-- Both are nullable with no default (unlike audio_policy, which always has
-- a value): captions are an opt-in toggle, not a mandatory pipeline stage,
-- so "not applicable" is represented as NULL, not a sentinel string.

alter table public.media_assemblies
  add column if not exists caption_policy text,
  add column if not exists caption_manifest jsonb;

alter table public.media_assemblies
  add constraint media_assemblies_caption_policy_known_value
    check (caption_policy is null or caption_policy in ('captions-burned-v1'));

-- A caption manifest is only meaningful alongside the policy that produced
-- it; a COMPLETED row never has one without naming the policy (and never
-- claims a policy with no manifest to show for it). A FAILED row is exempt:
-- caption_policy may be set (captions were requested) with no manifest
-- (nothing was actually produced) - see the column comment above.
alter table public.media_assemblies
  add constraint media_assemblies_caption_manifest_requires_policy
    check (status = 'failed' or (caption_manifest is null) = (caption_policy is null));

comment on column public.media_assemblies.caption_policy is
  'Which caption policy produced this row (e.g. captions-burned-v1), or NULL if captions were not requested for this assembly.';
comment on column public.media_assemblies.caption_manifest is
  'NULL unless caption_policy is set: one JSON object per caption segment, on the FINAL video''s own timeline, reproducing exactly what was burned in.';

-- save_media_assembly: same contract as before, plus the two new fields.
-- `payload -> 'caption_manifest'` is passed through `nullif(..., 'null'::jsonb)`
-- because `research.assembly_schema.AssemblyRecord.to_payload()` sends a
-- literal JSON null (not an omitted key) when captions were not requested -
-- `->` alone would otherwise store the JSON scalar `null` in the jsonb
-- column rather than a genuine SQL NULL.
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
     output_mime_type, error_message, source_voice_asset_ids, audio_policy, scene_manifest,
     caption_policy, caption_manifest, completed_at)
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
     payload ->> 'caption_policy', nullif(payload -> 'caption_manifest', 'null'::jsonb),
     case when payload ->> 'status' = 'completed' then now() else null end)
  returning id into new_id;

  return jsonb_build_object('status', 'created', 'assembly_id', new_id);
end $$;

revoke all on function public.save_media_assembly(jsonb) from public, anon, authenticated;
grant execute on function public.save_media_assembly(jsonb) to service_role;
