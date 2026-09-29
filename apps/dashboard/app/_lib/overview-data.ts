// Data access for the Overview / command-centre page. Server-side only, same
// Supabase server client as every other page. Every number here is a real
// count from the database - nothing is fabricated, and a stage with no data
// yet is represented honestly (an explicit `implemented: false` on the
// pipeline stage itself covers Distribution/Performance; the counts below
// simply come back 0 or null for those, which the page must not disguise as
// a "healthy zero").

import type { createClient } from "@/supabase/server";
import type { PipelineStageId } from "@/lib/nav";

type Supabase = Awaited<ReturnType<typeof createClient>>;

export type StageCount = { value: number | null; error: string | null };

export type PipelineCounts = Record<PipelineStageId, StageCount>;

async function headCount(
  query: PromiseLike<{ count: number | null; error: { message: string } | null }>,
): Promise<StageCount> {
  const { count, error } = await query;
  return error ? { value: null, error: error.message } : { value: count ?? 0, error: null };
}

export async function loadPipelineCounts(supabase: Supabase): Promise<PipelineCounts> {
  const head = { count: "exact", head: true } as const;
  const [research, insights, opportunities, production, media] = await Promise.all([
    headCount(supabase.from("research_jobs").select("id", head)),
    headCount(supabase.from("insights").select("id", head)),
    headCount(supabase.from("opportunities").select("id", head)),
    headCount(supabase.from("productions").select("id", head)),
    headCount(supabase.from("media_assets").select("id", head).eq("status", "completed")),
  ]);
  return {
    research,
    insights,
    opportunities,
    production,
    media,
    distribution: { value: null, error: null }, // stage does not exist yet
    performance: { value: null, error: null },
  };
}

// --- recent activity -----------------------------------------------------------------

export type ActivityKind =
  | "research_job_completed"
  | "synthesis_created"
  | "opportunity_run_created"
  | "production_run_created"
  | "media_asset_generated";

export type ActivityItem = {
  kind: ActivityKind;
  label: string;
  detail: string | null;
  href: string;
  at: string; // ISO timestamp
};

export async function loadRecentActivity(supabase: Supabase, limit = 8): Promise<ActivityItem[]> {
  const [jobs, syntheses, opportunityRuns, productionRuns, mediaAssets] = await Promise.all([
    supabase
      .from("research_jobs")
      .select("id, query, completed_at")
      .not("completed_at", "is", null)
      .order("completed_at", { ascending: false })
      .limit(limit),
    supabase
      .from("syntheses")
      .select("id, research_job_id, model, created_at")
      .order("created_at", { ascending: false })
      .limit(limit),
    supabase
      .from("opportunity_runs")
      .select("id, research_job_id, created_at")
      .order("created_at", { ascending: false })
      .limit(limit),
    supabase
      .from("production_runs")
      .select("id, created_at")
      .order("created_at", { ascending: false })
      .limit(limit),
    supabase
      .from("media_assets")
      .select("id, scene_number, status, created_at")
      .order("created_at", { ascending: false })
      .limit(limit),
  ]);

  const items: ActivityItem[] = [];
  for (const job of jobs.data ?? []) {
    items.push({
      kind: "research_job_completed",
      label: "Research job completed",
      detail: job.query,
      href: `/research/${job.id}`,
      at: job.completed_at as string,
    });
  }
  for (const s of syntheses.data ?? []) {
    items.push({
      kind: "synthesis_created",
      label: "Synthesis created",
      detail: s.model,
      href: `/research/insights?job=${s.research_job_id}&synthesis=${s.id}`,
      at: s.created_at,
    });
  }
  for (const r of opportunityRuns.data ?? []) {
    items.push({
      kind: "opportunity_run_created",
      label: "Opportunities created",
      detail: null,
      href: "/research/opportunities",
      at: r.created_at,
    });
  }
  for (const r of productionRuns.data ?? []) {
    items.push({
      kind: "production_run_created",
      label: "Production generated",
      detail: null,
      href: "/research/production",
      at: r.created_at,
    });
  }
  for (const a of mediaAssets.data ?? []) {
    items.push({
      kind: "media_asset_generated",
      label: a.status === "completed" ? "Media asset generated" : "Media generation failed",
      detail: `Scene ${a.scene_number}`,
      href: "/research/media",
      at: a.created_at,
    });
  }

  return items.sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).slice(0, limit);
}

// --- current work ----------------------------------------------------------------------

export type CurrentWorkItem = {
  label: string;
  title: string;
  meta: string | null;
  href: string;
  at: string;
} | null;

export type CurrentWork = {
  researchJob: CurrentWorkItem;
  synthesis: CurrentWorkItem;
  opportunityRun: CurrentWorkItem;
  productionRun: CurrentWorkItem;
  mediaRun: CurrentWorkItem;
};

export async function loadCurrentWork(supabase: Supabase): Promise<CurrentWork> {
  const [job, synthesis, opportunityRun, productionRun, mediaRun] = await Promise.all([
    supabase
      .from("research_jobs")
      .select("id, query, status, created_at")
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle(),
    supabase
      .from("syntheses")
      .select("id, research_job_id, model, created_at")
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle(),
    supabase
      .from("opportunity_runs")
      .select("id, research_job_id, product_key, created_at")
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle(),
    supabase
      .from("production_runs")
      .select("id, model, created_at")
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle(),
    supabase
      .from("media_runs")
      .select("id, image_model, created_at")
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle(),
  ]);

  return {
    researchJob: job.data
      ? {
          label: "Latest research job",
          title: job.data.query ?? "Untitled",
          meta: job.data.status,
          href: `/research/${job.data.id}`,
          at: job.data.created_at,
        }
      : null,
    synthesis: synthesis.data
      ? {
          label: "Latest synthesis",
          title: synthesis.data.model,
          meta: null,
          href: `/research/insights?job=${synthesis.data.research_job_id}&synthesis=${synthesis.data.id}`,
          at: synthesis.data.created_at,
        }
      : null,
    opportunityRun: opportunityRun.data
      ? {
          label: "Latest opportunity run",
          title: opportunityRun.data.product_key,
          meta: null,
          href: "/research/opportunities",
          at: opportunityRun.data.created_at,
        }
      : null,
    productionRun: productionRun.data
      ? {
          label: "Latest production run",
          title: productionRun.data.model,
          meta: null,
          href: "/research/production",
          at: productionRun.data.created_at,
        }
      : null,
    mediaRun: mediaRun.data
      ? {
          label: "Latest media run",
          title: mediaRun.data.image_model,
          meta: null,
          href: "/research/media",
          at: mediaRun.data.created_at,
        }
      : null,
  };
}
