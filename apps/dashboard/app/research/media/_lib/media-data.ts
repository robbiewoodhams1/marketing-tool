// Pure grouping/aggregation/formatting logic for the Media page, kept
// separate from the page component so it can be unit tested with
// `node --test` (mirrors app/research/_lib/synthesis.ts's split).

export type MediaAssetRow = {
  id: string;
  media_run_id: string;
  production_id: string;
  scene_number: number;
  asset_type: string;
  audio_subtype: string | null;
  status: string;
  provider: string;
  model: string;
  prompt: string | null;
  generation_config: unknown;
  duration_seconds: number | null;
  storage_url: string | null;
  error_message: string | null;
  cost_amount: number | null;
  cost_currency: string | null;
  created_at: string;
};

export type MediaRunRow = {
  id: string;
  production_id: string;
  image_provider: string | null;
  image_model: string | null;
  video_provider: string | null;
  video_model: string | null;
  voice_provider: string | null;
  voice_model: string | null;
  scene_count: number;
  created_at: string;
};

export type CostTotal = { currency: string; amount: number };

export type MediaRunGroup = {
  runId: string;
  productionId: string;
  provider: string;
  model: string;
  createdAt: string;
  sceneCount: number;
  assets: MediaAssetRow[];
  totalCosts: CostTotal[]; // normally 0 or 1 entries; more than 1 only if a run
  // somehow mixed currencies (a run is one provider/model, so this should not
  // happen in practice, but the aggregation never silently mixes them either way).
};

/** A run is for exactly one media type (research.media / research.voice's
 * own guarantee): its provider/model come from whichever of the image/
 * video/voice pair is populated, never more than one. */
export function runProviderModel(
  run: Pick<
    MediaRunRow,
    "image_provider" | "image_model" | "video_provider" | "video_model" | "voice_provider" | "voice_model"
  >,
): { provider: string; model: string } {
  if (run.image_provider && run.image_model) return { provider: run.image_provider, model: run.image_model };
  if (run.video_provider && run.video_model) return { provider: run.video_provider, model: run.video_model };
  if (run.voice_provider && run.voice_model) return { provider: run.voice_provider, model: run.voice_model };
  return { provider: "unknown", model: "unknown" };
}

/** Sums cost per currency, ignoring assets with no reported cost (never
 * treating a missing cost as zero - it is "unknown", not "free"). Kept
 * separate per currency rather than ever silently combined. */
export function sumCosts(assets: Pick<MediaAssetRow, "cost_amount" | "cost_currency">[]): CostTotal[] {
  const totals = new Map<string, number>();
  for (const asset of assets) {
    if (asset.cost_amount == null || !asset.cost_currency) continue;
    totals.set(asset.cost_currency, (totals.get(asset.cost_currency) ?? 0) + asset.cost_amount);
  }
  return [...totals.entries()].map(([currency, amount]) => ({ currency, amount }));
}

export function groupAssetsByRun(runs: MediaRunRow[], assets: MediaAssetRow[]): MediaRunGroup[] {
  const assetsByRun = new Map<string, MediaAssetRow[]>();
  for (const asset of assets) {
    const list = assetsByRun.get(asset.media_run_id);
    if (list) list.push(asset);
    else assetsByRun.set(asset.media_run_id, [asset]);
  }
  return runs.map((run) => {
    const runAssets = (assetsByRun.get(run.id) ?? [])
      .slice()
      .sort((a, b) => a.scene_number - b.scene_number || a.created_at.localeCompare(b.created_at));
    const { provider, model } = runProviderModel(run);
    return {
      runId: run.id,
      productionId: run.production_id,
      provider,
      model,
      createdAt: run.created_at,
      sceneCount: run.scene_count,
      assets: runAssets,
      totalCosts: sumCosts(runAssets),
    };
  });
}

/** "Sensible UK formatting" (en-GB locale conventions) applied to whatever
 * currency the provider's own rate is actually denominated in - the amount
 * is never silently converted to a different currency, and never rounded to
 * a fixed 2dp when the real figure needs more precision (e.g. a fraction of
 * a penny/cent per generation). */
export function formatCost(amount: number | null, currency: string | null): string {
  if (amount == null || !currency) return "—";
  try {
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    }).format(amount);
  } catch {
    return `${amount} ${currency}`;
  }
}

/** Distinct scene numbers with at least one COMPLETED asset - not a raw
 * asset-row count, which can exceed the scene count once a retry has left a
 * failed row alongside the eventual completed one for the same scene. */
export function completedSceneCount(assets: Pick<MediaAssetRow, "scene_number" | "status">[]): number {
  return new Set(assets.filter((a) => a.status === "completed").map((a) => a.scene_number)).size;
}

export function formatCostTotals(totals: CostTotal[]): string {
  if (totals.length === 0) return "—";
  return totals.map((t) => formatCost(t.amount, t.currency)).join(" + ");
}

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const whole = Math.round(seconds);
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/** Reads the voice/language a voiceover asset was generated with out of its
 * `generation_config` (an untyped jsonb column) - null when absent or the
 * column isn't shaped as expected, never a guess. */
export function voiceInfo(generationConfig: unknown): { voice: string | null; language: string | null } {
  if (!generationConfig || typeof generationConfig !== "object") return { voice: null, language: null };
  const config = generationConfig as Record<string, unknown>;
  return {
    voice: typeof config.voice === "string" ? config.voice : null,
    language: typeof config.language === "string" ? config.language : null,
  };
}
