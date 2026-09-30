// Pure grouping/formatting logic for the Assembly section of the Media page,
// kept separate from the page component so it can be unit tested with
// `node --test` (mirrors media-data.ts's own split).

export type SceneManifestEntry = {
  scene_number: number;
  video_asset_id: string;
  voice_asset_id: string;
  video_duration_seconds: number;
  voice_duration_seconds: number;
  scene_duration_seconds: number;
  sync_strategy: string;
};

export type CaptionManifestEntry = {
  index: number;
  scene_number: number;
  text: string;
  start_seconds: number;
  end_seconds: number;
  timing_algorithm: string;
};

export type AssemblyRow = {
  id: string;
  production_id: string;
  status: string;
  source_scene_count: number;
  source_duration_seconds: number | null;
  output_duration_seconds: number | null;
  output_storage_url: string | null;
  output_mime_type: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  // Assembly V2 only (research.assembly_v2) - null/empty for a V1,
  // picture-only assembly (research.assembly).
  source_voice_asset_ids: string[] | null;
  audio_policy: string | null;
  scene_manifest: SceneManifestEntry[] | null;
  // Captions V1 (research.captions) - null unless captions were requested
  // for this assembly (optional, unlike audio_policy).
  caption_policy: string | null;
  caption_manifest: CaptionManifestEntry[] | null;
};

/** Whether this assembly row has a synchronised voiceover track (Assembly
 * V2) rather than being a muted, picture-only V1 result. */
export function hasVoiceover(assembly: AssemblyRow): boolean {
  return (assembly.source_voice_asset_ids?.length ?? 0) > 0;
}

/** Whether this assembly's final output has captions burned in. */
export function hasCaptions(assembly: AssemblyRow): boolean {
  return assembly.caption_policy != null;
}

/** The most recent assembly attempt for each production - a retry after a
 * failure adds a new row (assemblies are immutable/append-only), so "the
 * current state of a production's assembly" is its latest row. */
export function latestAssemblyByProduction(assemblies: AssemblyRow[]): Map<string, AssemblyRow> {
  const latest = new Map<string, AssemblyRow>();
  for (const a of assemblies) {
    const existing = latest.get(a.production_id);
    if (!existing || a.created_at > existing.created_at) latest.set(a.production_id, a);
  }
  return latest;
}

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return "unknown";
  const whole = Math.round(seconds);
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}
