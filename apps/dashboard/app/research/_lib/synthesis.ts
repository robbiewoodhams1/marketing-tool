// View-model for Research Synthesis v1. Pure and dependency-free (apart from the
// classification parser) so it can be unit tested.
//
// The chain is kept explicit and never flattened:
//   RAW SOURCE (content, comment)  ->  INTERPRETATION  ->  SYNTHESIS  ->  INSIGHT
// Evidence that cites raw text is marked "raw"; evidence that cites an
// interpretation is marked "derived". Everything shown was stored by the
// backend; nothing is recomputed or invented here, and missing data stays
// missing (a NULL is never displayed as 0).

import {
  CLASSIFICATION_FIELDS,
  parseClassificationResult,
  type ClassificationField,
  type ClassificationFieldName,
} from "./classification.ts";

// --- rows as stored ----------------------------------------------------------

export type SynthesisCoverage = {
  contentTotal: number | null;
  classifiedContentCount: number | null;
  classificationCoverage: number | null; // fraction 0-1, as supplied by the backend
  commentsAvailable: number | null;
  commentsShown: number | null;
};

export type SynthesisRun = {
  id: string;
  researchJobId: string;
  researchJobQuery: string | null;
  model: string;
  promptVersion: string;
  schemaVersion: string;
  createdAt: string;
  coverage: SynthesisCoverage | null;
  noInsightsReason: string | null;
  insightCount: number | null;
};

export type EvidenceRole = "supports" | "counter";

export type EvidenceRow = {
  id: string;
  role: EvidenceRole;
  observationIndex: number | null;
  contentId: string | null;
  commentId: string | null;
  interpretationId: string | null;
  field: string;
  quote: string;
};

export type InsightRow = {
  id: string;
  title: string;
  patternType: string;
  statement: string;
  explanation: string | null;
  modelConfidence: number | null;
  confidenceRationale: string | null;
  limitations: string | null;
  observations: unknown;
  support: unknown;
  createdAt: string;
  evidence: EvidenceRow[];
};

// --- resolved sources (looked up by the data layer) ---------------------------

export type ContentSource = {
  id: string;
  title: string | null;
  creator: string | null;
  platform: string | null;
  url: string | null;
};

export type CommentSource = {
  id: string;
  contentId: string | null;
  text: string | null;
  likes: number | null;
  type: string | null;
};

export type InterpretationSource = {
  id: string;
  contentId: string | null;
  model: string | null;
  promptVersion: string | null;
  schemaVersion: string | null;
  createdAt: string | null;
  result: unknown;
};

export type Sources = {
  content: Map<string, ContentSource>;
  comments: Map<string, CommentSource>;
  interpretations: Map<string, InterpretationSource>;
};

// --- view types ---------------------------------------------------------------

export type SourceLink = {
  title: string | null;
  creator: string | null;
  platform: string | null;
  url: string | null; // only ever an http(s) URL
};

export type EvidenceOrigin = "raw" | "derived" | "unresolved";

type EvidenceBase = {
  id: string;
  role: EvidenceRole;
  origin: EvidenceOrigin;
  originLabel: string;
  fieldLabel: string;
  quote: string;
};

export type EvidenceView =
  | (EvidenceBase & { kind: "content"; content: SourceLink | null })
  | (EvidenceBase & {
      kind: "comment";
      comment: { text: string | null; likes: number | null; type: string | null } | null;
      content: SourceLink | null;
    })
  | (EvidenceBase & {
      kind: "interpretation";
      interpretation: {
        provenance: string | null;
        createdAt: string | null;
        // the classification fields the quote refers to, with what the
        // classifier itself recorded (value, confidence, evidence)
        fields: { name: ClassificationFieldName; label: string; field: ClassificationField }[];
      } | null;
      content: SourceLink | null;
    })
  | (EvidenceBase & { kind: "unresolved"; reason: string });

export type ObservationView = {
  index: number;
  text: string;
  evidence: EvidenceView[];
};

export type SupportItem = { label: string; value: string };

export type InsightView = {
  id: string;
  title: string;
  pattern: { value: string; label: string };
  statement: string;
  explanation: string | null;
  confidenceText: string; // "Confidence: 0.86" - the synthesis model's own estimate
  confidenceRationale: string | null;
  limitations: string | null;
  support: SupportItem[] | null; // null = support data unreadable
  statisticsCited: string[];
  observations: ObservationView[];
  otherSupporting: EvidenceView[]; // supporting evidence not tied to a listed observation
  counterEvidence: EvidenceView[];
  createdAt: string;
  problems: string[]; // things that could not be read, shown rather than hidden
};

// --- small helpers -------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

// numeric columns may arrive as numbers or numeric strings; anything else is
// unknown (null) - never coerced to 0.
function num(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function humanise(value: string): string {
  const text = value.replace(/_/g, " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : value;
}

const PATTERN_LABELS: Record<string, string> = {
  recurring_theme: "Recurring theme",
  hook_pattern: "Hook pattern",
  format_pattern: "Format pattern",
  performance_signal: "Performance signal",
  content_comment_pattern: "Content & comment pattern",
};

export function patternLabel(patternType: string): string {
  return PATTERN_LABELS[patternType] ?? humanise(patternType);
}

const FIELD_LABELS: Record<string, string> = {
  title: "Title",
  description: "Description",
  transcript: "Transcript",
  metrics: "Metrics",
  classification: "Classification",
  comment_text: "Comment text",
  statistics: "Dataset statistics",
};

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? humanise(field);
}

export function confidenceText(value: number | null): string {
  return value === null ? "Confidence unavailable" : `Confidence: ${value.toFixed(2)}`;
}

function percent(fraction: number): string {
  return `${Math.round(fraction * 1000) / 10}%`;
}

export function insightsHref(jobId: string, synthesisId?: string): string {
  const base = `/research/insights?job=${encodeURIComponent(jobId)}`;
  return synthesisId ? `${base}&synthesis=${encodeURIComponent(synthesisId)}` : base;
}

// --- parsing stored rows (defensive: a malformed row must never crash the page) ---

function parseCoverage(raw: unknown): SynthesisCoverage | null {
  if (!isRecord(raw)) return null;
  return {
    contentTotal: num(raw.content_total),
    classifiedContentCount: num(raw.classified_content_count),
    classificationCoverage: num(raw.classification_coverage),
    commentsAvailable: num(raw.comments_available),
    commentsShown: num(raw.comments_shown),
  };
}

export function parseSynthesisRun(raw: unknown): SynthesisRun | null {
  if (!isRecord(raw)) return null;
  const id = str(raw.id);
  const researchJobId = str(raw.research_job_id);
  const createdAt = str(raw.created_at);
  if (!id || !researchJobId || !createdAt) return null;
  const counted = Array.isArray(raw.insights) && isRecord(raw.insights[0])
    ? num(raw.insights[0].count)
    : null;
  const job = isRecord(raw.research_jobs) ? raw.research_jobs : null;
  return {
    id,
    researchJobId,
    researchJobQuery: job ? str(job.query) : null,
    model: str(raw.model) ?? "unknown model",
    promptVersion: str(raw.prompt_version) ?? "unknown",
    schemaVersion: str(raw.schema_version) ?? "unknown",
    createdAt,
    coverage: parseCoverage(raw.coverage),
    noInsightsReason: str(raw.no_insights_reason),
    insightCount: counted,
  };
}

export function parseSynthesisRuns(rows: unknown): SynthesisRun[] {
  return (Array.isArray(rows) ? rows : [])
    .map(parseSynthesisRun)
    .filter((r): r is SynthesisRun => r !== null);
}

function time(value: string): number {
  const t = Date.parse(value);
  return Number.isNaN(t) ? Number.NEGATIVE_INFINITY : t;
}

// created_at DESC, then id DESC: the same deterministic rule as classification.
export function newestFirst(a: SynthesisRun, b: SynthesisRun): number {
  const byTime = time(b.createdAt) - time(a.createdAt);
  if (byTime !== 0 && !Number.isNaN(byTime)) return byTime;
  return a.id < b.id ? 1 : a.id > b.id ? -1 : 0;
}

export type RunSelection =
  | { kind: "none" } // no synthesis has been run for this job
  | { kind: "not-found"; requested: string } // ?synthesis= names no run of this job
  | { kind: "selected"; run: SynthesisRun; isLatest: boolean; runs: SynthesisRun[] };

// The run to display: the requested one if it belongs to this job, otherwise
// the latest. The database distinguishes "never run" (no row) from "ran and
// found nothing" (a row with no insights); this preserves that distinction.
export function selectRun(runs: SynthesisRun[], requested?: string | null): RunSelection {
  const ordered = [...runs].sort(newestFirst);
  if (ordered.length === 0) return { kind: "none" };
  if (requested) {
    const run = ordered.find((r) => r.id === requested);
    return run
      ? { kind: "selected", run, isLatest: run.id === ordered[0].id, runs: ordered }
      : { kind: "not-found", requested };
  }
  return { kind: "selected", run: ordered[0], isLatest: true, runs: ordered };
}

export function parseEvidenceRow(raw: unknown): EvidenceRow | null {
  if (!isRecord(raw)) return null;
  const id = str(raw.id);
  const role = raw.role === "supports" || raw.role === "counter" ? raw.role : null;
  const field = str(raw.field);
  const quote = str(raw.quote);
  if (!id || !role || field === null || quote === null) return null;
  const index = num(raw.observation_index);
  return {
    id,
    role,
    observationIndex: index !== null && Number.isInteger(index) && index >= 0 ? index : null,
    contentId: str(raw.content_id),
    commentId: str(raw.comment_id),
    interpretationId: str(raw.interpretation_id),
    field,
    quote,
  };
}

export function parseInsightRow(raw: unknown): { row: InsightRow; dropped: number } | null {
  if (!isRecord(raw)) return null;
  const id = str(raw.id);
  const title = str(raw.title);
  const statement = str(raw.statement);
  if (!id || title === null || statement === null) return null;
  const rawEvidence = Array.isArray(raw.insight_evidence) ? raw.insight_evidence : [];
  const evidence = rawEvidence.map(parseEvidenceRow).filter((e): e is EvidenceRow => e !== null);
  return {
    dropped: rawEvidence.length - evidence.length,
    row: {
      id,
      title,
      patternType: str(raw.pattern_type) ?? "unknown",
      statement,
      explanation: str(raw.explanation),
      modelConfidence: num(raw.model_confidence),
      confidenceRationale: str(raw.confidence_rationale),
      limitations: str(raw.limitations),
      observations: raw.observations,
      support: raw.support,
      createdAt: str(raw.created_at) ?? "",
      evidence,
    },
  };
}

// --- support ---------------------------------------------------------------------

// Renders the counts the backend computed. Nothing is recomputed; a missing or
// non-numeric value is omitted rather than shown as 0.
export function supportItems(raw: unknown): {
  items: SupportItem[];
  statisticsCited: string[];
} | null {
  if (!isRecord(raw)) return null;
  const items: SupportItem[] = [];
  const contentCount = num(raw.content_count);
  const contentTotal = num(raw.content_total);
  if (contentCount !== null) {
    items.push({
      label: "Videos supporting",
      value: contentTotal !== null ? `${contentCount} of ${contentTotal}` : `${contentCount}`,
    });
  }
  const share = num(raw.share_of_content);
  if (share !== null) items.push({ label: "Share of content", value: percent(share) });
  const creators = num(raw.distinct_creators);
  if (creators !== null) items.push({ label: "Distinct creators", value: `${creators}` });
  const comments = num(raw.comment_count);
  if (comments !== null) items.push({ label: "Comments cited", value: `${comments}` });
  const interpretations = num(raw.interpretation_count);
  if (interpretations !== null) {
    items.push({ label: "Classifications cited", value: `${interpretations}` });
  }
  const classified = num(raw.classified_content_count);
  const coverage = num(raw.classification_coverage);
  if (classified !== null || coverage !== null) {
    const parts: string[] = [];
    if (classified !== null) {
      parts.push(contentTotal !== null ? `${classified} of ${contentTotal} classified` : `${classified} classified`);
    }
    if (coverage !== null) parts.push(percent(coverage));
    items.push({ label: "Classification coverage", value: parts.join(" · ") });
  }
  const counter = num(raw.counter_evidence_count);
  if (counter !== null) items.push({ label: "Counter-evidence items", value: `${counter}` });
  const cited = Array.isArray(raw.statistics_cited)
    ? raw.statistics_cited.filter((s): s is string => typeof s === "string")
    : [];
  return { items, statisticsCited: cited };
}

// --- evidence resolution ----------------------------------------------------------

function contentLink(sources: Sources, contentId: string | null): SourceLink | null {
  const c = contentId ? sources.content.get(contentId) : undefined;
  return c
    ? { title: c.title, creator: c.creator, platform: c.platform, url: safeHttpUrl(c.url) }
    : null;
}

// The classification fields a quote refers to. The stored classification text
// is "name: value | confidence .. | evidence: ..", so a quote names a field by
// its "name:" prefix. If none is named, none is shown (nothing is guessed).
export function matchedClassificationFields(
  quote: string,
  result: unknown,
): { name: ClassificationFieldName; label: string; field: ClassificationField }[] {
  const parsed = parseClassificationResult(result);
  if (!parsed) return [];
  return CLASSIFICATION_FIELDS.filter((name) => quote.includes(`${name}:`)).map((name) => ({
    name,
    label: humanise(name),
    field: parsed[name],
  }));
}

export function resolveEvidence(row: EvidenceRow, sources: Sources): EvidenceView {
  const base = { id: row.id, role: row.role, fieldLabel: fieldLabel(row.field), quote: row.quote };

  if (row.commentId) {
    const c = sources.comments.get(row.commentId);
    return {
      ...base,
      kind: "comment",
      origin: "raw",
      originLabel: "Raw source · comment",
      comment: c ? { text: c.text, likes: c.likes, type: c.type } : null,
      content: contentLink(sources, c?.contentId ?? null),
    };
  }
  if (row.interpretationId) {
    const i = sources.interpretations.get(row.interpretationId);
    const provenance = i && (i.model || i.promptVersion)
      ? [i.model, i.promptVersion].filter(Boolean).join(" · ")
      : null;
    return {
      ...base,
      kind: "interpretation",
      origin: "derived",
      originLabel: "Derived analysis · classification",
      interpretation: i
        ? {
            provenance,
            createdAt: i.createdAt,
            fields: matchedClassificationFields(row.quote, i.result),
          }
        : null,
      content: contentLink(sources, i?.contentId ?? null),
    };
  }
  if (row.contentId) {
    return {
      ...base,
      kind: "content",
      origin: "raw",
      originLabel: "Raw source · content",
      content: contentLink(sources, row.contentId),
    };
  }
  return {
    ...base,
    kind: "unresolved",
    origin: "unresolved",
    originLabel: "Source not recorded",
    reason: "This evidence row does not point at any source.",
  };
}

// --- the insight view ----------------------------------------------------------------

function parseObservations(raw: unknown): { texts: string[]; problem: string | null } {
  if (!Array.isArray(raw)) return { texts: [], problem: "Observations could not be read." };
  const texts: string[] = [];
  let bad = 0;
  for (const item of raw) {
    const text = isRecord(item) ? str(item.text) : typeof item === "string" ? item : null;
    if (text && text.trim()) texts.push(text);
    else bad++;
  }
  return {
    texts,
    problem: bad ? `${bad} observation${bad === 1 ? "" : "s"} could not be read.` : null,
  };
}

export function buildInsightView(row: InsightRow, sources: Sources, dropped = 0): InsightView {
  const problems: string[] = [];
  const { texts, problem } = parseObservations(row.observations);
  if (problem) problems.push(problem);
  if (dropped) {
    problems.push(`${dropped} evidence row${dropped === 1 ? "" : "s"} could not be read.`);
  }

  const support = supportItems(row.support);
  if (!support) problems.push("Support data could not be read.");

  const supports = row.evidence.filter((e) => e.role === "supports");
  const byObservation = new Map<number, EvidenceView[]>();
  const otherSupporting: EvidenceView[] = [];
  for (const e of supports) {
    const view = resolveEvidence(e, sources);
    if (e.observationIndex !== null && e.observationIndex < texts.length) {
      byObservation.set(e.observationIndex, [...(byObservation.get(e.observationIndex) ?? []), view]);
    } else {
      otherSupporting.push(view);
    }
  }
  const observations = texts.map((text, index) => ({
    index,
    text,
    evidence: byObservation.get(index) ?? [],
  }));

  return {
    id: row.id,
    title: row.title,
    pattern: { value: row.patternType, label: patternLabel(row.patternType) },
    statement: row.statement,
    explanation: row.explanation,
    confidenceText: confidenceText(row.modelConfidence),
    confidenceRationale: row.confidenceRationale,
    limitations: row.limitations,
    support: support ? support.items : null,
    statisticsCited: support ? support.statisticsCited : [],
    observations,
    otherSupporting,
    counterEvidence: row.evidence.filter((e) => e.role === "counter").map((e) => resolveEvidence(e, sources)),
    createdAt: row.createdAt,
    problems,
  };
}

// --- source maps from raw query results -------------------------------------------------

export function buildSources(input: {
  content: unknown;
  comments: unknown;
  interpretations: unknown;
}): Sources {
  const rows = (v: unknown) => (Array.isArray(v) ? v.filter(isRecord) : []);
  const sources: Sources = { content: new Map(), comments: new Map(), interpretations: new Map() };
  for (const r of rows(input.content)) {
    const id = str(r.id);
    if (id) {
      sources.content.set(id, {
        id,
        title: str(r.title),
        creator: str(r.creator),
        platform: str(r.platform),
        url: str(r.url),
      });
    }
  }
  for (const r of rows(input.comments)) {
    const id = str(r.id);
    if (id) {
      sources.comments.set(id, {
        id,
        contentId: str(r.content_id),
        text: str(r.text),
        likes: num(r.likes),
        type: str(r.type),
      });
    }
  }
  for (const r of rows(input.interpretations)) {
    const id = str(r.id);
    if (id) {
      sources.interpretations.set(id, {
        id,
        contentId: str(r.content_id),
        model: str(r.model),
        promptVersion: str(r.prompt_version),
        schemaVersion: str(r.schema_version),
        createdAt: str(r.created_at),
        result: r.result,
      });
    }
  }
  return sources;
}

// ids the data layer must look up, so a whole synthesis needs a bounded number of queries
export function referencedIds(rows: InsightRow[]): {
  contentIds: string[];
  commentIds: string[];
  interpretationIds: string[];
} {
  const content = new Set<string>();
  const comments = new Set<string>();
  const interpretations = new Set<string>();
  for (const row of rows) {
    for (const e of row.evidence) {
      if (e.contentId) content.add(e.contentId);
      if (e.commentId) comments.add(e.commentId);
      if (e.interpretationId) interpretations.add(e.interpretationId);
    }
  }
  return {
    contentIds: [...content],
    commentIds: [...comments],
    interpretationIds: [...interpretations],
  };
}

// --- provenance ------------------------------------------------------------------------

export function coverageLines(coverage: SynthesisCoverage | null): SupportItem[] {
  if (!coverage) return [];
  const lines: SupportItem[] = [];
  if (coverage.contentTotal !== null) {
    lines.push({ label: "Content analysed", value: `${coverage.contentTotal}` });
  }
  if (coverage.classifiedContentCount !== null || coverage.classificationCoverage !== null) {
    const parts: string[] = [];
    if (coverage.classifiedContentCount !== null) {
      parts.push(
        coverage.contentTotal !== null
          ? `${coverage.classifiedContentCount} of ${coverage.contentTotal} classified`
          : `${coverage.classifiedContentCount} classified`,
      );
    }
    if (coverage.classificationCoverage !== null) parts.push(percent(coverage.classificationCoverage));
    lines.push({ label: "Classification coverage", value: parts.join(" · ") });
  }
  if (coverage.commentsShown !== null) {
    lines.push({
      label: "Comments shown to the model",
      value:
        coverage.commentsAvailable !== null
          ? `${coverage.commentsShown} of ${coverage.commentsAvailable}`
          : `${coverage.commentsShown}`,
    });
  }
  return lines;
}

// What the page says about the run itself, using the actual run state.
export function runOutcome(run: SynthesisRun, insightsLoaded: number): {
  kind: "insights" | "found-nothing";
  message: string;
} {
  if (insightsLoaded > 0) return { kind: "insights", message: "" };
  return {
    kind: "found-nothing",
    message: run.noInsightsReason
      ? `The synthesis completed and found no sufficiently supported insights: ${run.noInsightsReason}`
      : "The synthesis completed and found no insights (no reason was recorded).",
  };
}
