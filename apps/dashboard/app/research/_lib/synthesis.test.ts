// Run with `pnpm test` (Node's built-in runner; no dependencies).
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import {
  buildInsightView,
  buildSources,
  confidenceText,
  coverageLines,
  insightsHref,
  matchedClassificationFields,
  newestFirst,
  parseEvidenceRow,
  parseInsightRow,
  parseSynthesisRun,
  parseSynthesisRuns,
  patternLabel,
  referencedIds,
  resolveEvidence,
  runOutcome,
  safeHttpUrl,
  selectRun,
  supportItems,
  type EvidenceRow,
  type InsightRow,
  type SynthesisRun,
} from "./synthesis.ts";

// --- fixtures ---------------------------------------------------------------------------

const field = (value: string | null, confidence: number | null = 0.9, evidence: string | null = "ev") =>
  value === null
    ? { value: null, confidence: null, evidence: null }
    : { value, confidence, evidence };

const classification = () => ({
  topic: field("tax"),
  audience: field("sole traders"),
  pain_point: field("penalties"),
  hook: field("Stop"),
  hook_type: field("fear", 0.88, "now required"),
  format: field("tutorial"),
  emotion: field("urgency"),
  cta: field(null),
});

const SOURCES = buildSources({
  content: [
    { id: "content-1", title: "MTD for sole traders", creator: "AccountsCo", platform: "youtube", url: "https://www.youtube.com/watch?v=abc" },
    { id: "content-2", title: "MTD explained", creator: "AccountsCo", platform: "youtube", url: "javascript:alert(1)" },
    { id: "content-3", title: null, creator: null, platform: null, url: null },
  ],
  comments: [{ id: "comment-1", content_id: "content-1", text: "I am terrified of the penalties and fines", likes: 12, type: "top_level" }],
  interpretations: [{
    id: "interp-1", content_id: "content-2", model: "claude-haiku-4-5-20251001",
    prompt_version: "classification-v2", schema_version: "classification-v1",
    created_at: "2026-09-26T21:54:03+00:00", result: classification(),
  }],
});

const ev = (over: Partial<EvidenceRow> = {}): EvidenceRow => ({
  id: "e1", role: "supports", observationIndex: 0, contentId: "content-1",
  commentId: null, interpretationId: null, field: "title", quote: "MTD for sole traders", ...over,
});

const insight = (over: Partial<InsightRow> = {}): InsightRow => ({
  id: "i1",
  title: "MTD is framed as a requirement",
  patternType: "hook_pattern",
  statement: "Among the analysed videos, two frame MTD as a requirement.",
  explanation: "Both name MTD.",
  modelConfidence: 0.86,
  confidenceRationale: "Two videos in a small sample.",
  limitations: "Four videos only.",
  observations: [{ text: "C1 opens with the requirement." }, { text: "C2 is titled as an explanation." }],
  support: {
    content_count: 2, content_total: 4, share_of_content: 0.5, distinct_creators: 1,
    comment_count: 1, interpretation_count: 1, classified_content_count: 3,
    classification_coverage: 0.75, counter_evidence_count: 1,
    statistics_cited: ["metrics.views.median: 3300.0"], computed_by: "code",
  },
  createdAt: "2026-09-29T10:00:00+00:00",
  evidence: [
    ev({ id: "e1", observationIndex: 0 }),
    ev({ id: "e2", observationIndex: 1, contentId: null, commentId: "comment-1", field: "comment_text", quote: "terrified of the penalties" }),
    ev({ id: "e3", observationIndex: 1, contentId: null, interpretationId: "interp-1", field: "classification", quote: "hook_type: fear" }),
    ev({ id: "e4", role: "counter", observationIndex: null, contentId: "content-3", quote: "Untitled" }),
  ],
  ...over,
});

const run = (over: Partial<SynthesisRun> = {}): SynthesisRun => ({
  id: "run-1", researchJobId: "job-1", researchJobQuery: "UK sole trader accounting",
  model: "claude-haiku-4-5-20251001", promptVersion: "synthesis-v1", schemaVersion: "synthesis-v1",
  createdAt: "2026-09-29T10:00:00+00:00",
  coverage: { contentTotal: 10, classifiedContentCount: 7, classificationCoverage: 0.7, commentsAvailable: 339, commentsShown: 60 },
  noInsightsReason: null, insightCount: 1, ...over,
});

// --- 1-3. statement, legacy fields, confidence ---------------------------------------------------

test("an insight is rendered from `statement` and the model_confidence column", () => {
  const raw = {
    id: "i1", title: "t", statement: "Among the analysed videos, X.", model_confidence: 0.86,
    pattern_type: "hook_pattern", observations: [], support: {}, created_at: "2026-09-29T10:00:00+00:00",
    insight_evidence: [],
  };
  const parsed = parseInsightRow(raw);
  assert.ok(parsed);
  const view = buildInsightView(parsed.row, SOURCES);
  assert.equal(view.statement, "Among the analysed videos, X.");
  assert.equal(view.confidenceText, "Confidence: 0.86");
});

test("the removed legacy fields are neither required nor read", () => {
  const raw = { id: "i1", title: "t", statement: "s", description: "OLD", evidence_summary: "OLD", confidence: 0.1, insight_evidence: [] };
  const parsed = parseInsightRow(raw);
  assert.ok(parsed);
  const view = buildInsightView(parsed.row, SOURCES);
  assert.equal(view.statement, "s"); // never falls back to `description`
  assert.equal(view.confidenceText, "Confidence unavailable"); // never reads the old `confidence`
  assert.ok(!JSON.stringify(view).includes("OLD"));
  assert.equal(parseInsightRow({ id: "i1", title: "t", description: "only legacy" }), null); // needs `statement`
});

test("confidence renders consistently and unknown is never shown as zero", () => {
  assert.equal(confidenceText(0.86), "Confidence: 0.86");
  assert.equal(confidenceText(0.6), "Confidence: 0.60");
  assert.equal(confidenceText(1), "Confidence: 1.00");
  assert.equal(confidenceText(0), "Confidence: 0.00"); // a genuine zero
  assert.equal(confidenceText(null), "Confidence unavailable");
  const stringNumeric = parseInsightRow({ id: "i", title: "t", statement: "s", model_confidence: "0.6", insight_evidence: [] });
  assert.equal(stringNumeric?.row.modelConfidence, 0.6); // numeric columns may arrive as strings
  for (const bad of [null, undefined, "high", "", NaN, {}]) {
    assert.equal(parseInsightRow({ id: "i", title: "t", statement: "s", model_confidence: bad })?.row.modelConfidence, null);
  }
});

// --- 4-5. pattern type and limitations ----------------------------------------------------------------

test("pattern type renders with a label, and unknown types are still shown", () => {
  assert.equal(patternLabel("hook_pattern"), "Hook pattern");
  assert.equal(patternLabel("content_comment_pattern"), "Content & comment pattern");
  assert.equal(patternLabel("future_pattern_kind"), "Future pattern kind");
  assert.equal(buildInsightView(insight(), SOURCES).pattern.label, "Hook pattern");
});

test("limitations, explanation and rationale render as stored; absent ones stay absent", () => {
  const view = buildInsightView(insight(), SOURCES);
  assert.equal(view.limitations, "Four videos only.");
  assert.equal(view.confidenceRationale, "Two videos in a small sample.");
  assert.equal(view.explanation, "Both name MTD.");
  const bare = buildInsightView(insight({ limitations: null, explanation: null, confidenceRationale: null }), SOURCES);
  assert.deepEqual([bare.limitations, bare.explanation, bare.confidenceRationale], [null, null, null]);
});

// --- 6. support -----------------------------------------------------------------------------------------

test("support statistics render as labelled, backend-supplied values", () => {
  const view = buildInsightView(insight(), SOURCES);
  assert.deepEqual(view.support, [
    { label: "Videos supporting", value: "2 of 4" },
    { label: "Share of content", value: "50%" },
    { label: "Distinct creators", value: "1" },
    { label: "Comments cited", value: "1" },
    { label: "Classifications cited", value: "1" },
    { label: "Classification coverage", value: "3 of 4 classified · 75%" },
    { label: "Counter-evidence items", value: "1" },
  ]);
  assert.deepEqual(view.statisticsCited, ["metrics.views.median: 3300.0"]);
});

test("support never invents statistics: missing or non-numeric values are omitted, not zero", () => {
  const partial = supportItems({ content_count: 3, distinct_creators: null, comment_count: "many", share_of_content: undefined });
  assert.deepEqual(partial?.items, [{ label: "Videos supporting", value: "3" }]);
  assert.deepEqual(supportItems({})?.items, []);
  assert.ok(!JSON.stringify(supportItems({ content_count: 2 })).includes('"0"'));
  const zero = supportItems({ comment_count: 0 });
  assert.deepEqual(zero?.items, [{ label: "Comments cited", value: "0" }]); // a genuine zero is kept
});

test("unreadable support is reported, not dumped or crashed on", () => {
  for (const bad of [null, "text", 5, [], undefined]) {
    const view = buildInsightView(insight({ support: bad }), SOURCES);
    assert.equal(view.support, null);
    assert.ok(view.problems.includes("Support data could not be read."));
  }
});

// --- 7-9. observations, evidence, counter-evidence -------------------------------------------------------------

test("observations render in order with their own evidence", () => {
  const view = buildInsightView(insight(), SOURCES);
  assert.deepEqual(view.observations.map((o) => [o.index, o.text, o.evidence.length]), [
    [0, "C1 opens with the requirement.", 1],
    [1, "C2 is titled as an explanation.", 2],
  ]);
});

test("evidence carries its source, field and verbatim quote", () => {
  const [first] = buildInsightView(insight(), SOURCES).observations[0].evidence;
  assert.equal(first.quote, "MTD for sole traders");
  assert.equal(first.fieldLabel, "Title");
  assert.equal(first.role, "supports");
  assert.equal(first.origin, "raw");
  assert.equal(first.originLabel, "Raw source · content");
});

test("counter-evidence is separate from supporting evidence and only present when stored", () => {
  const view = buildInsightView(insight(), SOURCES);
  assert.deepEqual(view.counterEvidence.map((e) => [e.id, e.role]), [["e4", "counter"]]);
  assert.ok(view.observations.every((o) => o.evidence.every((e) => e.role === "supports")));
  const none = buildInsightView(insight({ evidence: insight().evidence.filter((e) => e.role === "supports") }), SOURCES);
  assert.deepEqual(none.counterEvidence, []);
});

// --- 10-12. resolving sources ------------------------------------------------------------------------------------

test("content evidence resolves to the video and only ever links http(s) URLs", () => {
  const good = resolveEvidence(ev(), SOURCES);
  assert.equal(good.kind, "content");
  if (good.kind !== "content") return;
  assert.deepEqual(good.content, { title: "MTD for sole traders", creator: "AccountsCo", platform: "youtube", url: "https://www.youtube.com/watch?v=abc" });
  const unsafe = resolveEvidence(ev({ contentId: "content-2" }), SOURCES);
  assert.ok(unsafe.kind === "content" && unsafe.content?.url === null); // javascript: is dropped
  assert.equal(safeHttpUrl("https://x.test/a"), "https://x.test/a");
  for (const bad of ["javascript:alert(1)", "data:text/html,x", "ftp://x", "not a url", null, 5]) {
    assert.equal(safeHttpUrl(bad), null);
  }
});

test("comment evidence resolves to the comment and the video it is on", () => {
  const r = resolveEvidence(ev({ contentId: null, commentId: "comment-1", field: "comment_text", quote: "terrified of the penalties" }), SOURCES);
  assert.equal(r.kind, "comment");
  if (r.kind !== "comment") return;
  assert.equal(r.origin, "raw");
  assert.deepEqual(r.comment, { text: "I am terrified of the penalties and fines", likes: 12, type: "top_level" });
  assert.equal(r.content?.title, "MTD for sole traders");
  assert.equal(r.fieldLabel, "Comment text");
});

test("interpretation evidence is marked as derived analysis with its provenance and fields", () => {
  const r = resolveEvidence(ev({ contentId: null, interpretationId: "interp-1", field: "classification", quote: "hook_type: fear | confidence 0.88" }), SOURCES);
  assert.equal(r.kind, "interpretation");
  if (r.kind !== "interpretation") return;
  assert.equal(r.origin, "derived");
  assert.equal(r.originLabel, "Derived analysis · classification");
  assert.equal(r.interpretation?.provenance, "claude-haiku-4-5-20251001 · classification-v2");
  assert.equal(r.interpretation?.createdAt, "2026-09-26T21:54:03+00:00");
  assert.deepEqual(r.interpretation?.fields.map((f) => [f.name, f.field.value, f.field.confidence, f.field.evidence]), [
    ["hook_type", "fear", 0.88, "now required"],
  ]);
  assert.equal(r.content?.title, "MTD explained"); // the video that was classified
});

test("raw evidence and derived analysis are never conflated", () => {
  const views = buildInsightView(insight(), SOURCES).observations[1].evidence;
  assert.deepEqual(views.map((v) => [v.kind, v.origin]), [["comment", "raw"], ["interpretation", "derived"]]);
});

test("only the classification fields a quote names are shown; nothing is guessed", () => {
  const result = classification();
  assert.deepEqual(matchedClassificationFields("hook_type: fear and format: tutorial", result).map((f) => f.name), ["hook_type", "format"]);
  assert.deepEqual(matchedClassificationFields("some paraphrase", result), []);
  assert.deepEqual(matchedClassificationFields("hook_type: fear", { topic: "malformed" }), []);
  assert.deepEqual(matchedClassificationFields("hook_type: fear", null), []);
});

// --- 13. missing evidence handled gracefully -----------------------------------------------------------------------

test("an observation with no evidence rows is shown as such, not hidden", () => {
  const view = buildInsightView(insight({ evidence: [] }), SOURCES);
  assert.equal(view.observations.length, 2);
  assert.ok(view.observations.every((o) => o.evidence.length === 0));
});

test("evidence whose source cannot be found still shows its quote and does not throw", () => {
  const missing = [
    ev({ id: "a", contentId: "content-404" }),
    ev({ id: "b", contentId: null, commentId: "comment-404", field: "comment_text" }),
    ev({ id: "c", contentId: null, interpretationId: "interp-404", field: "classification" }),
  ];
  const views = missing.map((e) => resolveEvidence(e, SOURCES));
  assert.ok(views.every((v) => v.quote === "MTD for sole traders"));
  const [a, b, c] = views;
  assert.ok(a.kind === "content" && a.content === null);
  assert.ok(b.kind === "comment" && b.comment === null && b.content === null);
  assert.ok(c.kind === "interpretation" && c.interpretation === null && c.content === null);
});

test("evidence that points at nothing is flagged unresolved", () => {
  const r = resolveEvidence(ev({ contentId: null }), SOURCES);
  assert.equal(r.kind, "unresolved");
  assert.equal(r.origin, "unresolved");
});

test("supporting evidence not tied to a listed observation is kept, not dropped", () => {
  const view = buildInsightView(insight({ evidence: [ev({ id: "x", observationIndex: 9 }), ev({ id: "y", observationIndex: null })] }), SOURCES);
  assert.deepEqual(view.otherSupporting.map((e) => e.id), ["x", "y"]);
});

test("a source shown for one row is never mixed into another (maps are by id)", () => {
  const sources = buildSources({ content: [{ id: "c1", title: "One" }, { id: "c2", title: "Two" }], comments: [], interpretations: [] });
  assert.equal((resolveEvidence(ev({ contentId: "c2" }), sources) as { content: { title: string } }).content.title, "Two");
});

// --- 14. no synthesis vs a synthesis that found nothing ------------------------------------------------------------------

test("'no synthesis run' and 'synthesis found nothing' are different states", () => {
  assert.deepEqual(selectRun([]), { kind: "none" });
  const found = selectRun([run({ insightCount: 0, noInsightsReason: "The evidence does not support any recurring pattern." })]);
  assert.equal(found.kind, "selected"); // a run row exists even though it has no insights
  if (found.kind !== "selected") return;
  const outcome = runOutcome(found.run, 0);
  assert.equal(outcome.kind, "found-nothing");
  assert.match(outcome.message, /completed and found no sufficiently supported insights: The evidence does not support/);
  assert.match(runOutcome(run(), 0).message, /completed and found no insights \(no reason was recorded\)/);
  assert.equal(runOutcome(run(), 3).kind, "insights");
});

test("the run to show: newest by default, a requested run of this job, else not-found", () => {
  const old = run({ id: "old", createdAt: "2026-09-01T00:00:00+00:00", model: "m-old" });
  const mid = run({ id: "mid", createdAt: "2026-09-10T00:00:00+00:00" });
  const latest = run({ id: "new", createdAt: "2026-09-29T00:00:00+00:00" });
  for (const order of [[old, mid, latest], [latest, old, mid], [mid, latest, old]]) {
    const sel = selectRun(order);
    assert.ok(sel.kind === "selected" && sel.run.id === "new" && sel.isLatest && sel.runs.length === 3);
  }
  const older = selectRun([old, mid, latest], "old");
  assert.ok(older.kind === "selected" && older.run.model === "m-old" && !older.isLatest);
  assert.deepEqual(selectRun([old, latest], "someone-elses-run"), { kind: "not-found", requested: "someone-elses-run" });
  const a = run({ id: "a", createdAt: "2026-09-29T00:00:00+00:00" });
  const b = run({ id: "b", createdAt: "2026-09-29T00:00:00+00:00" });
  assert.ok(newestFirst(a, b) > 0 && newestFirst(b, a) < 0); // ties broken by id, deterministically
});

test("provenance and coverage come from the stored run, with unknowns omitted", () => {
  const parsed = parseSynthesisRun({
    id: "s1", research_job_id: "j1", model: "claude-haiku-4-5-20251001", prompt_version: "synthesis-v1",
    schema_version: "synthesis-v1", created_at: "2026-09-29T10:00:00+00:00",
    coverage: { content_total: 10, classified_content_count: 7, classification_coverage: 0.7, comments_available: 339, comments_shown: 60 },
    no_insights_reason: null, insights: [{ count: 3 }], research_jobs: { query: "UK sole trader accounting" },
  });
  assert.ok(parsed);
  assert.deepEqual([parsed.model, parsed.promptVersion, parsed.schemaVersion, parsed.insightCount, parsed.researchJobQuery],
    ["claude-haiku-4-5-20251001", "synthesis-v1", "synthesis-v1", 3, "UK sole trader accounting"]);
  assert.deepEqual(coverageLines(parsed.coverage), [
    { label: "Content analysed", value: "10" },
    { label: "Classification coverage", value: "7 of 10 classified · 70%" },
    { label: "Comments shown to the model", value: "60 of 339" },
  ]);
  assert.deepEqual(coverageLines(null), []);
  assert.deepEqual(coverageLines({ contentTotal: null, classifiedContentCount: null, classificationCoverage: null, commentsAvailable: null, commentsShown: null }), []);
});

// --- 15. job-scoped navigation -----------------------------------------------------------------------------------------------

test("insight links are job-scoped and safely encoded", () => {
  assert.equal(insightsHref("job-1"), "/research/insights?job=job-1");
  assert.equal(insightsHref("job-1", "run-2"), "/research/insights?job=job-1&synthesis=run-2");
  assert.equal(insightsHref("a b&c"), "/research/insights?job=a%20b%26c");
});

test("the job page links to that job's insights and the insights page reuses the job-scope pattern", () => {
  const jobPage = readFileSync(join(appDir, "[id]", "page.tsx"), "utf8");
  assert.match(jobPage, /insightsHref\(job\.id\)/);
  assert.match(jobPage, /No synthesis has been run for this job yet/);
  assert.match(jobPage, /\/research\/content\?job=/); // existing navigation is intact
  assert.match(jobPage, /\/research\/comments\?job=/);
  const page = readFileSync(join(appDir, "insights", "page.tsx"), "utf8");
  assert.match(page, /resolveJobScope\(supabase, params\.job\)/);
  assert.match(page, /<JobScopeBanner scope=\{scope\} noun="Insights" \/>/);
});

// --- 16. malformed / partial data never crashes -------------------------------------------------------------------------------

test("malformed stored data degrades instead of throwing", () => {
  const garbage: unknown[] = [null, undefined, 5, "text", [], {}, [null, 1, "x"], { id: 7 }, { insight_evidence: "no" }];
  for (const g of garbage) {
    assert.doesNotThrow(() => parseSynthesisRun(g));
    assert.doesNotThrow(() => parseInsightRow(g));
    assert.doesNotThrow(() => parseEvidenceRow(g));
    assert.doesNotThrow(() => supportItems(g));
    assert.doesNotThrow(() => buildSources({ content: g, comments: g, interpretations: g }));
    assert.doesNotThrow(() => selectRun(parseSynthesisRuns(g)));
  }
  assert.deepEqual(parseSynthesisRuns("nope"), []);
  assert.deepEqual(parseSynthesisRuns([null, { id: "s" }]), []);
});

test("a partly malformed insight still renders what it can and says what it could not read", () => {
  const raw = {
    id: "i1", title: "t", statement: "s", model_confidence: null,
    observations: [{ text: "ok" }, { nope: true }, 5, { text: "  " }, "plain string observation"],
    support: "broken", created_at: "x",
    insight_evidence: [
      { id: "good", role: "supports", observation_index: 0, content_id: "content-1", field: "title", quote: "q" },
      { id: "bad-role", role: "weird", field: "title", quote: "q" },
      { role: "supports", field: "title", quote: "q" },
      "junk",
    ],
  };
  const parsed = parseInsightRow(raw);
  assert.ok(parsed);
  assert.equal(parsed.dropped, 3);
  const view = buildInsightView(parsed.row, SOURCES, parsed.dropped);
  assert.deepEqual(view.observations.map((o) => o.text), ["ok", "plain string observation"]);
  assert.equal(view.observations[0].evidence.length, 1);
  assert.equal(view.confidenceText, "Confidence unavailable");
  assert.equal(view.support, null);
  assert.deepEqual(view.problems, [
    "3 observations could not be read.",
    "3 evidence rows could not be read.",
    "Support data could not be read.",
  ]);
});

test("non-array observations are reported, not thrown on", () => {
  for (const bad of [null, "x", 5, {}]) {
    const view = buildInsightView(insight({ observations: bad }), SOURCES);
    assert.deepEqual(view.observations, []);
    assert.ok(view.problems.includes("Observations could not be read."));
  }
});

// --- data access -------------------------------------------------------------------------------------------------------------------

test("referenced sources are collected once across all insights (no N+1)", () => {
  const rows = [insight(), insight({ id: "i2" })];
  const ids = referencedIds(rows);
  assert.deepEqual(ids.contentIds.sort(), ["content-1", "content-3"]);
  assert.deepEqual(ids.commentIds, ["comment-1"]);
  assert.deepEqual(ids.interpretationIds, ["interp-1"]);
});

// --- contract scans: no stale references to the removed schema -------------------------------------------------------------------------

const appDir = new URL("..", import.meta.url).pathname; // app/research/

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !name.endsWith(".test.ts") ? [path] : [];
  });
}

test("no dashboard source references the removed insight columns", () => {
  for (const file of sources(appDir)) {
    assert.ok(!readFileSync(file, "utf8").includes("evidence_summary"), `${file} references evidence_summary`);
  }
});

test("insight queries select only columns that exist in the current schema", () => {
  const data = readFileSync(join(appDir, "_lib", "synthesis-data.ts"), "utf8");
  const columns = /const INSIGHT_COLUMNS =([\s\S]*?);/.exec(data)?.[1] ?? "";
  for (const needed of ["statement", "model_confidence", "pattern_type", "confidence_rationale", "limitations", "observations", "support", "insight_evidence("]) {
    assert.ok(columns.includes(needed), `missing ${needed}`);
  }
  for (const removed of ["evidence_summary", "updated_at"]) assert.ok(!columns.includes(removed));
  assert.ok(!/\bdescription\b/.test(columns) && !/\bconfidence\b/.test(columns)); // the old names
  for (const file of sources(appDir)) {
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(/\.from\("insights"\)\s*\.select\(\s*"([^"]*)"/g)) {
      assert.ok(!/\b(description|evidence_summary|updated_at|confidence)\b/.test(m[1]), `${file}: ${m[1]}`);
    }
  }
});

test("the detail view uses a bounded number of queries, independent of insight count", () => {
  const data = readFileSync(join(appDir, "_lib", "synthesis-data.ts"), "utf8");
  const detail = data.slice(data.indexOf("export async function loadSynthesisDetail"));
  assert.equal((detail.match(/\.from\("insights"\)/g) ?? []).length, 1); // one query for all insights + evidence
  assert.equal((detail.match(/selectIn\(/g) ?? []).length, 3); // comments, interpretations, content
  assert.ok(!/\.map\([^)]*(supabase|selectIn)/.test(detail)); // no per-insight / per-evidence query
});

test("the insights page distinguishes no synthesis, a run that found nothing, and errors", () => {
  const page = readFileSync(join(appDir, "insights", "page.tsx"), "utf8");
  assert.match(page, /No synthesis has been run for this research job/);
  assert.match(page, /runOutcome\(run, insights\.length\)/);
  assert.match(page, /role="alert"/);
  for (const f of ["loading.tsx", "error.tsx"]) {
    assert.ok(readFileSync(join(appDir, "insights", f), "utf8").length > 0);
  }
  assert.match(readFileSync(join(appDir, "insights", "error.tsx"), "utf8"), /retry/); // this Next.js passes `retry`
});
