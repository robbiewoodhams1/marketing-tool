// Run with `npm test` (Node's built-in runner; no dependencies).
import assert from "node:assert/strict";
import { test } from "node:test";
import { formatDuration, hasCaptions, hasVoiceover, latestAssemblyByProduction, type AssemblyRow } from "./assembly-data.ts";

function assembly(overrides: Partial<AssemblyRow> = {}): AssemblyRow {
  return {
    id: "asm-1",
    production_id: "prod-1",
    status: "completed",
    source_scene_count: 6,
    source_duration_seconds: 36,
    output_duration_seconds: 36.2,
    output_storage_url: "https://example.com/final.mp4",
    output_mime_type: "video/mp4",
    error_message: null,
    created_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:00:05Z",
    source_voice_asset_ids: null,
    audio_policy: "mute-v1",
    scene_manifest: null,
    caption_policy: null,
    caption_manifest: null,
    ...overrides,
  };
}

test("latestAssemblyByProduction picks the most recent row per production", () => {
  const rows = [
    assembly({ id: "asm-old", created_at: "2026-01-01T00:00:00Z" }),
    assembly({ id: "asm-new", created_at: "2026-01-02T00:00:00Z" }),
  ];
  const latest = latestAssemblyByProduction(rows);
  assert.equal(latest.get("prod-1")?.id, "asm-new");
});

test("latestAssemblyByProduction keeps different productions separate", () => {
  const rows = [
    assembly({ id: "a", production_id: "prod-1" }),
    assembly({ id: "b", production_id: "prod-2" }),
  ];
  const latest = latestAssemblyByProduction(rows);
  assert.equal(latest.get("prod-1")?.id, "a");
  assert.equal(latest.get("prod-2")?.id, "b");
});

test("latestAssemblyByProduction of no rows is empty", () => {
  assert.equal(latestAssemblyByProduction([]).size, 0);
});

test("a failed retry after a completed attempt still becomes the latest shown", () => {
  // Assemblies are immutable/append-only: a later FAILED retry is still the
  // most recent state, and should be surfaced (not hidden behind an older success).
  const rows = [
    assembly({ id: "asm-ok", created_at: "2026-01-01T00:00:00Z", status: "completed" }),
    assembly({
      id: "asm-retry", created_at: "2026-01-02T00:00:00Z", status: "failed",
      output_storage_url: null, output_duration_seconds: null, completed_at: null,
      error_message: "FFmpegProcessError: boom",
    }),
  ];
  const latest = latestAssemblyByProduction(rows);
  assert.equal(latest.get("prod-1")?.id, "asm-retry");
  assert.equal(latest.get("prod-1")?.status, "failed");
});

test("formatDuration renders unknown for null", () => {
  assert.equal(formatDuration(null), "unknown");
});

test("formatDuration renders seconds-only under a minute", () => {
  assert.equal(formatDuration(42), "42s");
});

test("formatDuration renders minutes and seconds over a minute", () => {
  assert.equal(formatDuration(96), "1m 36s");
});

test("formatDuration rounds to the nearest whole second", () => {
  assert.equal(formatDuration(36.2), "36s");
  assert.equal(formatDuration(59.6), "1m 0s");
});

test("hasVoiceover is false for a V1 (mute), picture-only assembly", () => {
  assert.equal(hasVoiceover(assembly({ source_voice_asset_ids: null })), false);
  assert.equal(hasVoiceover(assembly({ source_voice_asset_ids: [] })), false);
});

test("hasVoiceover is true for a V2 (voiceover-synced) assembly", () => {
  assert.equal(
    hasVoiceover(assembly({ source_voice_asset_ids: ["a1", "a2"], audio_policy: "voiceover-sync-v1" })),
    true,
  );
});

test("hasCaptions is false when caption_policy is null", () => {
  assert.equal(hasCaptions(assembly({ caption_policy: null })), false);
});

test("hasCaptions is true when caption_policy is set", () => {
  assert.equal(hasCaptions(assembly({ caption_policy: "captions-burned-v1" })), true);
});
