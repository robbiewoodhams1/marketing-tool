// Pure grouping/formatting logic for the Assembly section of the Media page,
// kept separate from the page component so it can be unit tested with
// `node --test` (mirrors media-data.ts's own split).

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
};

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
