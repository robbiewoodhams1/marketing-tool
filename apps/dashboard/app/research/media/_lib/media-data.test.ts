// Run with `npm test` (Node's built-in runner; no dependencies).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  completedSceneCount,
  formatCost,
  formatCostTotals,
  formatDuration,
  groupAssetsByRun,
  runProviderModel,
  sumCosts,
  voiceInfo,
  type MediaAssetRow,
  type MediaRunRow,
} from "./media-data.ts";

function asset(overrides: Partial<MediaAssetRow> = {}): MediaAssetRow {
  return {
    id: "asset-1",
    media_run_id: "run-1",
    production_id: "prod-1",
    scene_number: 1,
    asset_type: "image",
    audio_subtype: null,
    status: "completed",
    provider: "gemini",
    model: "gemini-2.5-flash-image",
    prompt: null,
    generation_config: null,
    duration_seconds: null,
    storage_url: "https://example.com/a.png",
    error_message: null,
    cost_amount: null,
    cost_currency: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function run(overrides: Partial<MediaRunRow> = {}): MediaRunRow {
  return {
    id: "run-1",
    production_id: "prod-1",
    image_provider: "gemini",
    image_model: "gemini-2.5-flash-image",
    video_provider: null,
    video_model: null,
    voice_provider: null,
    voice_model: null,
    scene_count: 2,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

// --- runProviderModel --------------------------------------------------------------------------

test("an image run resolves to its image provider/model", () => {
  assert.deepEqual(runProviderModel(run()), { provider: "gemini", model: "gemini-2.5-flash-image" });
});

test("a video run resolves to its video provider/model", () => {
  const videoRun = run({ image_provider: null, image_model: null, video_provider: "veo", video_model: "veo-3.1-fast-generate-preview" });
  assert.deepEqual(runProviderModel(videoRun), { provider: "veo", model: "veo-3.1-fast-generate-preview" });
});

test("a voice run resolves to its voice provider/model", () => {
  const voiceRun = run({
    image_provider: null, image_model: null, voice_provider: "gemini", voice_model: "gemini-2.5-flash-preview-tts",
  });
  assert.deepEqual(runProviderModel(voiceRun), { provider: "gemini", model: "gemini-2.5-flash-preview-tts" });
});

// --- sumCosts ------------------------------------------------------------------------------------

test("sumCosts ignores assets with no reported cost - missing is not zero", () => {
  const totals = sumCosts([asset({ cost_amount: null, cost_currency: null }), asset({ cost_amount: 0.05, cost_currency: "USD" })]);
  assert.deepEqual(totals, [{ currency: "USD", amount: 0.05 }]);
});

test("sumCosts adds up multiple assets in the same currency", () => {
  const totals = sumCosts([
    asset({ cost_amount: 0.0387, cost_currency: "USD" }),
    asset({ cost_amount: 0.4, cost_currency: "USD" }),
  ]);
  assert.equal(totals.length, 1);
  assert.equal(totals[0].currency, "USD");
  assert.ok(Math.abs(totals[0].amount - 0.4387) < 1e-9);
});

test("sumCosts keeps different currencies separate rather than mixing them", () => {
  const totals = sumCosts([
    asset({ cost_amount: 0.04, cost_currency: "USD" }),
    asset({ cost_amount: 0.03, cost_currency: "GBP" }),
  ]);
  assert.equal(totals.length, 2);
  const byCurrency = Object.fromEntries(totals.map((t) => [t.currency, t.amount]));
  assert.equal(byCurrency.USD, 0.04);
  assert.equal(byCurrency.GBP, 0.03);
});

test("sumCosts of no costed assets is empty, not zero", () => {
  assert.deepEqual(sumCosts([asset(), asset()]), []);
});

// --- groupAssetsByRun ------------------------------------------------------------------------------

test("assets are grouped under their own run and sorted by scene number", () => {
  const runs = [run({ id: "run-1" })];
  const assets = [
    asset({ id: "a2", media_run_id: "run-1", scene_number: 2 }),
    asset({ id: "a1", media_run_id: "run-1", scene_number: 1 }),
  ];
  const [group] = groupAssetsByRun(runs, assets);
  assert.deepEqual(group.assets.map((a) => a.id), ["a1", "a2"]);
});

test("a run with no assets yet groups to an empty asset list, not an error", () => {
  const [group] = groupAssetsByRun([run({ id: "run-1" })], []);
  assert.deepEqual(group.assets, []);
  assert.deepEqual(group.totalCosts, []);
});

test("assets from a different run are never mixed into this one", () => {
  const runs = [run({ id: "run-1" }), run({ id: "run-2" })];
  const assets = [
    asset({ id: "a1", media_run_id: "run-1" }),
    asset({ id: "a2", media_run_id: "run-2" }),
  ];
  const groups = groupAssetsByRun(runs, assets);
  assert.deepEqual(groups.find((g) => g.runId === "run-1")?.assets.map((a) => a.id), ["a1"]);
  assert.deepEqual(groups.find((g) => g.runId === "run-2")?.assets.map((a) => a.id), ["a2"]);
});

test("a run's total cost sums only its own assets", () => {
  const runs = [run({ id: "run-1" }), run({ id: "run-2" })];
  const assets = [
    asset({ id: "a1", media_run_id: "run-1", cost_amount: 0.04, cost_currency: "USD" }),
    asset({ id: "a2", media_run_id: "run-2", cost_amount: 0.9, cost_currency: "USD" }),
  ];
  const groups = groupAssetsByRun(runs, assets);
  assert.deepEqual(groups.find((g) => g.runId === "run-1")?.totalCosts, [{ currency: "USD", amount: 0.04 }]);
  assert.deepEqual(groups.find((g) => g.runId === "run-2")?.totalCosts, [{ currency: "USD", amount: 0.9 }]);
});

test("a failed asset's cost (real spend before a later failure) still counts toward the run total", () => {
  const runs = [run({ id: "run-1" })];
  const assets = [
    asset({ id: "a1", media_run_id: "run-1", status: "completed", cost_amount: 0.04, cost_currency: "USD" }),
    asset({ id: "a2", media_run_id: "run-1", status: "failed", cost_amount: 0.04, cost_currency: "USD", storage_url: null, error_message: "storage upload failed" }),
  ];
  const [group] = groupAssetsByRun(runs, assets);
  assert.deepEqual(group.totalCosts, [{ currency: "USD", amount: 0.08 }]);
});

// --- completedSceneCount ---------------------------------------------------------------------------

test("completedSceneCount counts distinct scenes, not raw asset rows", () => {
  // Scene 1 has a failed row followed by a completed retry - two rows, one scene.
  const rows = [
    asset({ scene_number: 1, status: "failed" }),
    asset({ scene_number: 1, status: "completed" }),
    asset({ scene_number: 2, status: "completed" }),
  ];
  assert.equal(completedSceneCount(rows), 2);
});

test("completedSceneCount ignores scenes that only ever failed", () => {
  const rows = [asset({ scene_number: 1, status: "failed" }), asset({ scene_number: 2, status: "completed" })];
  assert.equal(completedSceneCount(rows), 1);
});

test("completedSceneCount of no assets is zero", () => {
  assert.equal(completedSceneCount([]), 0);
});

// --- formatCost / formatCostTotals ------------------------------------------------------------------

test("formatCost renders a null amount or currency as an em dash", () => {
  assert.equal(formatCost(null, "USD"), "—");
  assert.equal(formatCost(0.04, null), "—");
});

test("formatCost uses the stored currency's own symbol, in UK locale formatting", () => {
  assert.equal(formatCost(0.4, "USD"), "US$0.40");
  assert.equal(formatCost(0.5, "GBP"), "£0.50");
});

test("formatCost keeps precision beyond 2dp rather than rounding a fraction of a cent away", () => {
  assert.equal(formatCost(0.0387, "USD"), "US$0.0387");
});

test("formatCost never converts pounds/dollars into pence/cents", () => {
  const formatted = formatCost(0.1157, "GBP");
  assert.equal(formatted, "£0.1157");
  assert.ok(!formatted.includes("p"));
});

test("formatCostTotals renders nothing costed as an em dash", () => {
  assert.equal(formatCostTotals([]), "—");
});

test("formatCostTotals renders one currency total directly", () => {
  assert.equal(formatCostTotals([{ currency: "USD", amount: 0.4387 }]), "US$0.4387");
});

test("formatCostTotals renders multiple currencies joined, never summed together", () => {
  assert.equal(
    formatCostTotals([{ currency: "USD", amount: 0.4 }, { currency: "GBP", amount: 0.3 }]),
    "US$0.40 + £0.30",
  );
});

// --- formatDuration --------------------------------------------------------------------------------

test("formatDuration renders an em dash for null", () => {
  assert.equal(formatDuration(null), "—");
});

test("formatDuration renders seconds-only under a minute", () => {
  assert.equal(formatDuration(6), "6s");
});

test("formatDuration renders minutes and seconds over a minute", () => {
  assert.equal(formatDuration(96), "1m 36s");
});

// --- voiceInfo ---------------------------------------------------------------------------------------

test("voiceInfo reads the voice and language out of generation_config", () => {
  assert.deepEqual(voiceInfo({ voice: "Kore", language: "en-US" }), { voice: "Kore", language: "en-US" });
});

test("voiceInfo is null/null for null, non-object, or missing fields", () => {
  assert.deepEqual(voiceInfo(null), { voice: null, language: null });
  assert.deepEqual(voiceInfo("not an object"), { voice: null, language: null });
  assert.deepEqual(voiceInfo({ aspect_ratio: "9:16" }), { voice: null, language: null });
});
