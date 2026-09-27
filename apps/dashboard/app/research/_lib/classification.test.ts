// Run with `pnpm test` (Node's built-in runner; no dependencies).
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import {
  CLASSIFICATION_FIELDS,
  classificationView,
  describeField,
  describeProvenance,
  parseClassificationResult,
  type InterpretationRow,
} from "./classification.ts";

const field = (value: string | null, confidence = 0.9, evidence = "quote") =>
  value === null
    ? { value: null, confidence: null, evidence: null }
    : { value, confidence, evidence };

const result = (overrides: Record<string, unknown> = {}) => ({
  topic: field("tax"),
  audience: field("sole traders"),
  pain_point: field("penalties"),
  hook: field("Stop"),
  hook_type: field("fear", 0.88, "urgency"),
  format: field("tutorial"),
  emotion: field("urgency"),
  cta: field(null),
  ...overrides,
});

const row = (over: Partial<InterpretationRow> = {}): InterpretationRow => ({
  id: "00000000-0000-0000-0000-000000000001",
  analysis_type: "classification",
  model: "model-a",
  prompt_version: "classification-v2",
  schema_version: "classification-v1",
  result: result(),
  created_at: "2026-09-26T21:54:03+00:00",
  ...over,
});

// --- no interpretation ----------------------------------------------------------------------

test("content without an interpretation renders as not classified", () => {
  for (const rows of [[], null, undefined, "nope", {}, [{ junk: true }, 42, null]]) {
    assert.deepEqual(classificationView(rows), { kind: "none" });
  }
});

test("non-classification interpretations are ignored", () => {
  assert.deepEqual(classificationView([row({ analysis_type: "synthesis" })]), {
    kind: "none",
  });
});

// --- one interpretation ---------------------------------------------------------------------

test("content with an interpretation shows its classification and provenance", () => {
  const view = classificationView([row()]);
  assert.equal(view.kind, "ok");
  if (view.kind !== "ok") return;
  assert.equal(view.fields.hook_type.value, "fear");
  assert.equal(view.fields.hook_type.confidence, 0.88);
  assert.equal(view.fields.hook_type.evidence, "urgency");
  assert.deepEqual(Object.keys(view.fields), [...CLASSIFICATION_FIELDS]);
  assert.equal(view.provenance.model, "model-a");
  assert.equal(view.provenance.promptVersion, "classification-v2");
  assert.equal(view.provenance.schemaVersion, "classification-v1");
  assert.equal(view.provenance.createdAt, "2026-09-26T21:54:03+00:00");
  assert.equal(view.earlier, 0);
  assert.equal(describeProvenance(view.provenance), "model-a · classification-v2");
});

test("null values (insufficient evidence) are preserved, not invented", () => {
  const view = classificationView([row()]);
  assert.ok(view.kind === "ok");
  assert.deepEqual(view.fields.cta, { value: null, confidence: null, evidence: null });
  assert.equal(describeField(view.fields.cta), undefined);
  assert.equal(
    describeField(view.fields.hook_type),
    "confidence 0.88 — evidence: urgency",
  );
});

// --- multiple interpretations ---------------------------------------------------------------

test("multiple interpretations display the latest and count the earlier ones", () => {
  const older = row({ id: "a", model: "old", created_at: "2026-09-01T00:00:00+00:00" });
  const newest = row({ id: "b", model: "new", created_at: "2026-09-27T00:00:00+00:00" });
  const middle = row({ id: "c", model: "mid", created_at: "2026-09-10T00:00:00+00:00" });
  for (const rows of [[older, newest, middle], [newest, middle, older], [middle, older, newest]]) {
    const view = classificationView(rows);
    assert.ok(view.kind === "ok");
    assert.equal(view.provenance.model, "new");
    assert.equal(view.earlier, 2);
  }
});

test("equal timestamps are broken deterministically by id", () => {
  const t = "2026-09-27T00:00:00+00:00";
  const a = row({ id: "a", model: "A", created_at: t });
  const b = row({ id: "b", model: "B", created_at: t });
  for (const rows of [[a, b], [b, a]]) {
    const view = classificationView(rows);
    assert.ok(view.kind === "ok");
    assert.equal(view.provenance.model, "B");
  }
});

test("the latest interpretation is shown even if an older one is readable", () => {
  const good = row({ id: "a", created_at: "2026-09-01T00:00:00+00:00" });
  const bad = row({ id: "b", created_at: "2026-09-02T00:00:00+00:00", result: { topic: "x" } });
  const view = classificationView([good, bad]);
  assert.equal(view.kind, "unreadable"); // no silent fallback to an older result
  assert.ok(view.kind === "unreadable");
  assert.equal(view.earlier, 1);
});

// --- malformed / defensive ------------------------------------------------------------------

test("malformed or null results are unreadable, never partially shown", () => {
  const bad: unknown[] = [
    null,
    "text",
    [],
    {},
    result({ hook_type: "fear" }), // bare string, not an object
    result({ topic: null }), // whole field null is not the stored shape
    result({ topic: { value: 3, confidence: 0.5, evidence: "e" } }),
    result({ topic: { value: "x", confidence: "high", evidence: "e" } }),
    result({ topic: { value: "x", confidence: 0.5 } }), // missing evidence
    Object.fromEntries(Object.entries(result()).filter(([k]) => k !== "cta")), // missing field
  ];
  for (const r of bad) {
    assert.equal(parseClassificationResult(r), null);
    assert.equal(classificationView([row({ result: r })]).kind, "unreadable");
  }
});

test("rows that are not shaped like interpretations are ignored", () => {
  const view = classificationView([{ id: 1, analysis_type: "classification" }, row()]);
  assert.equal(view.kind, "ok");
});

// --- no frontend query references the removed content columns --------------------------------

const REMOVED = ["topic", "audience", "pain_point", "hook", "hook_type", "format", "emotion", "cta"];
const appDir = new URL("..", import.meta.url).pathname; // app/research/

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !name.endsWith(".test.ts") ? [path] : [];
  });
}

test("no query selects a removed column from content", () => {
  const selectFromContent = /from\("content"\)\s*\.select\(\s*(?:\/\/[^\n]*\n\s*)*"([^"]*)"/g;
  let checked = 0;
  for (const file of sources(appDir)) {
    for (const match of readFileSync(file, "utf8").matchAll(selectFromContent)) {
      checked++;
      const columns = match[1].replace(/interpretations[!\w]*\([^)]*\)/g, "");
      for (const name of REMOVED) {
        assert.ok(
          !new RegExp(`\\b${name}\\b`).test(columns),
          `${file} selects removed content column ${name}`,
        );
      }
    }
  }
  assert.ok(checked >= 2, "expected to find the content queries");
});

test("the content page reads classification only through the interpretations helper", () => {
  const page = readFileSync(join(appDir, "content", "page.tsx"), "utf8");
  assert.match(page, /interpretations\(/);
  assert.match(page, /classificationView\(item\.interpretations\)/);
  for (const name of REMOVED) {
    assert.ok(!new RegExp(`item\\.${name}\\b`).test(page), `item.${name} is read`);
  }
});

test("the job page counts analysed content via interpretations, not content columns", () => {
  const page = readFileSync(join(appDir, "[id]", "page.tsx"), "utf8");
  assert.ok(!page.includes("analysis_json"));
  assert.match(page, /interpretations!inner\(id\)/);
  assert.match(page, /"interpretations\.analysis_type", "classification"/);
});
