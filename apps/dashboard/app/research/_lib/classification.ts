// Classification is DERIVED analysis stored in `interpretations.result`; it is
// never read from `content`. Pure and dependency-free so it can be unit tested.
//
// Display rule: of a content row's interpretations with
// analysis_type = "classification", show the one with the greatest created_at
// (ties broken by id). Older ones stay in the database and are only counted.

export const CLASSIFICATION_ANALYSIS_TYPE = "classification";

export const CLASSIFICATION_FIELDS = [
  "topic",
  "audience",
  "pain_point",
  "hook",
  "hook_type",
  "format",
  "emotion",
  "cta",
] as const;

export type ClassificationFieldName = (typeof CLASSIFICATION_FIELDS)[number];

// `value` is null when the classifier had insufficient evidence.
export type ClassificationField = {
  value: string | null;
  confidence: number | null;
  evidence: string | null;
};

export type ClassificationResult = Record<
  ClassificationFieldName,
  ClassificationField
>;

// A row of `interpretations` as returned by Supabase. `result` is jsonb, so it
// is `unknown` until parseClassificationResult has validated it.
export type InterpretationRow = {
  id: string;
  analysis_type: string;
  model: string;
  prompt_version: string;
  schema_version: string;
  result: unknown;
  created_at: string;
};

export type Provenance = {
  id: string;
  model: string;
  promptVersion: string;
  schemaVersion: string;
  createdAt: string;
};

export type ClassificationView =
  | { kind: "none" }
  | {
      kind: "unreadable"; // the latest interpretation's result is malformed
      provenance: Provenance;
      earlier: number;
    }
  | {
      kind: "ok";
      provenance: Provenance;
      fields: ClassificationResult;
      earlier: number; // older classification interpretations not shown
    };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseField(value: unknown): ClassificationField | null {
  if (!isRecord(value)) return null;
  const { value: v, confidence, evidence } = value;
  if (v !== null && typeof v !== "string") return null;
  if (
    confidence !== null &&
    (typeof confidence !== "number" || !Number.isFinite(confidence))
  )
    return null;
  if (evidence !== null && typeof evidence !== "string") return null;
  return { value: v, confidence, evidence };
}

// Strict on shape: all eight fields must be present and well formed, otherwise
// the whole result is treated as unreadable (null) rather than half-shown.
export function parseClassificationResult(
  raw: unknown,
): ClassificationResult | null {
  if (!isRecord(raw)) return null;
  const out: Partial<ClassificationResult> = {};
  for (const name of CLASSIFICATION_FIELDS) {
    const field = parseField(raw[name]);
    if (!field) return null;
    out[name] = field;
  }
  return out as ClassificationResult;
}

function isInterpretationRow(raw: unknown): raw is InterpretationRow {
  return (
    isRecord(raw) &&
    typeof raw.id === "string" &&
    typeof raw.analysis_type === "string" &&
    typeof raw.model === "string" &&
    typeof raw.prompt_version === "string" &&
    typeof raw.schema_version === "string" &&
    typeof raw.created_at === "string"
  );
}

function time(value: string): number {
  const t = Date.parse(value);
  return Number.isNaN(t) ? Number.NEGATIVE_INFINITY : t;
}

// created_at DESC, then id DESC so the choice is deterministic.
function newestFirst(a: InterpretationRow, b: InterpretationRow): number {
  const byTime = time(b.created_at) - time(a.created_at);
  if (byTime !== 0 && !Number.isNaN(byTime)) return byTime;
  return a.id < b.id ? 1 : a.id > b.id ? -1 : 0;
}

// `rows` is whatever came back for one content row (null/undefined when there
// were none). Rows that are not classification interpretations, or are not
// even shaped like interpretation rows, are ignored.
export function classificationView(rows: unknown): ClassificationView {
  const classifications = (Array.isArray(rows) ? rows : [])
    .filter(isInterpretationRow)
    .filter((r) => r.analysis_type === CLASSIFICATION_ANALYSIS_TYPE)
    .sort(newestFirst);
  const latest = classifications[0];
  if (!latest) return { kind: "none" };

  const provenance: Provenance = {
    id: latest.id,
    model: latest.model,
    promptVersion: latest.prompt_version,
    schemaVersion: latest.schema_version,
    createdAt: latest.created_at,
  };
  const earlier = classifications.length - 1;
  const fields = parseClassificationResult(latest.result);
  return fields
    ? { kind: "ok", provenance, fields, earlier }
    : { kind: "unreadable", provenance, earlier };
}

// Short human-readable provenance, e.g. "claude-haiku-4-5 · classification-v2".
export function describeProvenance(p: Provenance): string {
  return `${p.model} · ${p.promptVersion}`;
}

// Tooltip text for one field: confidence and evidence, when present.
export function describeField(field: ClassificationField): string | undefined {
  const parts: string[] = [];
  if (field.confidence !== null)
    parts.push(`confidence ${field.confidence.toFixed(2)}`);
  if (field.evidence) parts.push(`evidence: ${field.evidence}`);
  return parts.length ? parts.join(" — ") : undefined;
}
