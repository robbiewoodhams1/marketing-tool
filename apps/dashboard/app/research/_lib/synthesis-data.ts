// Server-side data access for syntheses and their insights. Server components
// only: Supabase is read with the server client, as everywhere else in the
// dashboard. Nothing here talks to the Python service.
//
// Query budget for the detail view (no N+1, independent of the number of
// insights or evidence rows):
//   1. the job's synthesis runs
//   2. the chosen run's insights, with their evidence rows embedded
//   3. comments + interpretations cited as evidence        (in parallel)
//   4. the content those cite, directly or as their parent (chunked by id)

import type { createClient } from "@/supabase/server";
import {
  buildInsightView,
  buildSources,
  parseInsightRow,
  parseSynthesisRuns,
  referencedIds,
  type InsightRow,
  type InsightView,
  type SynthesisRun,
} from "./synthesis.ts";

type Supabase = Awaited<ReturnType<typeof createClient>>;

const RUN_COLUMNS =
  "id, research_job_id, analysis_type, model, prompt_version, schema_version, created_at, " +
  "coverage:input_manifest->coverage, no_insights_reason:result->>no_insights_reason, " +
  "insights(count)";

const INSIGHT_COLUMNS =
  "id, research_job_id, synthesis_id, title, pattern_type, statement, model_confidence, " +
  "confidence_rationale, explanation, limitations, observations, support, created_at, " +
  "insight_evidence(id, role, observation_index, content_id, comment_id, interpretation_id, field, quote)";

const ID_CHUNK = 50; // keeps `in (...)` URLs short

export type Loaded<T> = { ok: true; data: T } | { ok: false; error: string };

// All synthesis runs of one research job (newest first is decided by selectRun).
export async function loadRunsForJob(
  supabase: Supabase,
  jobId: string,
): Promise<Loaded<SynthesisRun[]>> {
  const { data, error } = await supabase
    .from("syntheses")
    .select(RUN_COLUMNS)
    .eq("research_job_id", jobId);
  if (error) return { ok: false, error: error.message };
  return { ok: true, data: parseSynthesisRuns(data) };
}

// The most recent runs across every job, for the unscoped overview.
export async function loadRecentRuns(
  supabase: Supabase,
  limit = 100,
): Promise<Loaded<SynthesisRun[]>> {
  const { data, error } = await supabase
    .from("syntheses")
    .select(`${RUN_COLUMNS}, research_jobs(query)`)
    .order("created_at", { ascending: false })
    .limit(limit);
  if (error) return { ok: false, error: error.message };
  return { ok: true, data: parseSynthesisRuns(data) };
}

async function selectIn(
  supabase: Supabase,
  table: "content" | "comments" | "interpretations",
  columns: string,
  ids: string[],
): Promise<Loaded<unknown[]>> {
  const rows: unknown[] = [];
  for (let i = 0; i < ids.length; i += ID_CHUNK) {
    const { data, error } = await supabase
      .from(table)
      .select(columns)
      .in("id", ids.slice(i, i + ID_CHUNK));
    if (error) return { ok: false, error: `${table}: ${error.message}` };
    rows.push(...(data ?? []));
  }
  return { ok: true, data: rows };
}

export type SynthesisDetail = { insights: InsightView[]; warnings: string[] };

// The insights of one run, each with its evidence resolved to real sources.
export async function loadSynthesisDetail(
  supabase: Supabase,
  synthesisId: string,
): Promise<Loaded<SynthesisDetail>> {
  const { data, error } = await supabase
    .from("insights")
    .select(INSIGHT_COLUMNS)
    .eq("synthesis_id", synthesisId)
    .order("created_at", { ascending: true })
    .order("id", { ascending: true });
  if (error) return { ok: false, error: error.message };

  const parsed = (Array.isArray(data) ? data : []).map(parseInsightRow);
  const warnings: string[] = [];
  const skipped = parsed.filter((p) => p === null).length;
  if (skipped) {
    warnings.push(`${skipped} insight${skipped === 1 ? "" : "s"} could not be read and ${skipped === 1 ? "is" : "are"} not shown.`);
  }
  const rows = parsed.filter((p): p is NonNullable<typeof p> => p !== null);
  const insightRows: InsightRow[] = rows.map((p) => p.row);
  const ids = referencedIds(insightRows);

  const [comments, interpretations] = await Promise.all([
    selectIn(supabase, "comments", "id, content_id, text, likes, type", ids.commentIds),
    selectIn(
      supabase,
      "interpretations",
      "id, content_id, model, prompt_version, schema_version, created_at, result",
      ids.interpretationIds,
    ),
  ]);
  for (const r of [comments, interpretations]) {
    if (!r.ok) warnings.push(`Some evidence sources could not be loaded (${r.error}).`);
  }

  // content cited directly, plus the video a cited comment/interpretation belongs to
  const contentIds = new Set(ids.contentIds);
  for (const list of [comments.ok ? comments.data : [], interpretations.ok ? interpretations.data : []]) {
    for (const row of list) {
      const id = (row as { content_id?: unknown }).content_id;
      if (typeof id === "string") contentIds.add(id);
    }
  }
  const content = await selectIn(
    supabase,
    "content",
    "id, title, creator, platform, url",
    [...contentIds],
  );
  if (!content.ok) warnings.push(`Some evidence sources could not be loaded (${content.error}).`);

  const sources = buildSources({
    content: content.ok ? content.data : [],
    comments: comments.ok ? comments.data : [],
    interpretations: interpretations.ok ? interpretations.data : [],
  });
  return {
    ok: true,
    data: {
      insights: rows.map((p) => buildInsightView(p.row, sources, p.dropped)),
      warnings,
    },
  };
}
