import Link from "next/link";
import { StatusBadge } from "../_components/status-badge";
import { formatDate, formatNumber } from "../_lib/format";
import {
  insightsHref,
  parseSynthesisRuns,
  selectRun,
  type SynthesisRun,
} from "../_lib/synthesis";
import { createClient } from "@/supabase/server";

type Supabase = Awaited<ReturnType<typeof createClient>>;
type Count = { value: number | null; error: string | null };

function BackLink() {
  return (
    <p>
      <Link href="/research" className="underline">
        Back to Research
      </Link>
    </p>
  );
}

// Every count is a head-only request, so no rows are transferred. A failed
// count is reported as an error, never shown as 0.
async function count(
  query: PromiseLike<{ count: number | null; error: { message: string } | null }>,
): Promise<Count> {
  const { count, error } = await query;
  return error
    ? { value: null, error: error.message }
    : { value: count ?? 0, error: null };
}

function loadCounts(supabase: Supabase, jobId: string) {
  const head = { count: "exact", head: true } as const;
  const content = () =>
    supabase.from("content").select("id", head).eq("research_job_id", jobId);
  return Promise.all([
    count(content()),
    // comments belong to a job through their content row
    count(
      supabase
        .from("comments")
        .select("id, content!inner(research_job_id)", head)
        .eq("content.research_job_id", jobId),
    ),
    count(content().not("transcript", "is", null)),
    // "Analysed" = the content has at least one classification interpretation
    // (derived analysis lives in `interpretations`, not on `content`). The
    // inner join keeps only such content rows, each counted once.
    count(
      supabase
        .from("content")
        .select("id, interpretations!inner(id)", head)
        .eq("research_job_id", jobId)
        .eq("interpretations.analysis_type", "classification"),
    ),
    count(
      supabase
        .from("opportunities")
        .select("id", head)
        .eq("research_job_id", jobId),
    ),
  ]);
}

type SynthesisSummary = {
  latest: SynthesisRun | null; // null: no synthesis has been run for this job
  runs: number;
  insights: Count; // insights of the latest run
  error: string | null;
};

// Insights belong to a synthesis run, and a job can have several runs (a new
// model or prompt version adds a run; it never replaces one). So the count is
// of the LATEST run's insights, not a sum across runs. Whether a synthesis
// exists comes from the syntheses table, not from an empty insights count.
async function loadSynthesisSummary(
  supabase: Supabase,
  jobId: string,
): Promise<SynthesisSummary> {
  const { data, error } = await supabase
    .from("syntheses")
    .select("id, research_job_id, model, prompt_version, schema_version, created_at")
    .eq("research_job_id", jobId);
  if (error) {
    return { latest: null, runs: 0, insights: { value: null, error: error.message }, error: error.message };
  }
  const selection = selectRun(parseSynthesisRuns(data));
  if (selection.kind !== "selected") {
    return { latest: null, runs: 0, insights: { value: 0, error: null }, error: null };
  }
  const insights = await count(
    supabase
      .from("insights")
      .select("id", { count: "exact", head: true })
      .eq("synthesis_id", selection.run.id),
  );
  return { latest: selection.run, runs: selection.runs.length, insights, error: null };
}

function Stat({
  label,
  count,
  href,
}: {
  label: string;
  count: Count;
  href?: string;
}) {
  const card = (
    <div
      className={`rounded border border-foreground/15 p-4${
        href ? " hover:bg-foreground/5" : ""
      }`}
    >
      <p className="text-2xl font-semibold">
        {count.error ? "—" : formatNumber(count.value)}
      </p>
      <p className="text-sm text-foreground/70">{label}</p>
      {count.error && (
        <p role="alert" className="mt-1 text-xs text-red-600">
          Error: {count.error}
        </p>
      )}
    </div>
  );
  return href ? (
    <Link href={href} className="block">
      {card}
    </Link>
  ) : (
    card
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-10">
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="text-sm text-foreground/60">{description}</p>
      <div className="mt-3">{children}</div>
    </section>
  );
}

export default async function ResearchJobPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await createClient();

  const { data: job, error } = await supabase
    .from("research_jobs")
    .select(
      "id, query, audience, objective, status, started_at, completed_at, created_at",
    )
    .eq("id", id)
    .maybeSingle();

  // An invalid UUID (e.g. a garbage path segment) fails at the database
  // level rather than returning an empty result — treat it the same as
  // "not found" instead of surfacing the raw Postgres error.
  const notFound = !error && !job;
  const isInvalidId = error?.code === "22P02";

  if (notFound || isInvalidId) {
    return (
      <main className="p-8">
        <p>Research job not found.</p>
        <div className="mt-4">
          <BackLink />
        </div>
      </main>
    );
  }

  if (error || !job) {
    return (
      <main className="p-8">
        <p role="alert" className="text-red-600">
          Error loading research job: {error?.message}
        </p>
        <div className="mt-4">
          <BackLink />
        </div>
      </main>
    );
  }

  const [[videos, comments, transcripts, analysed, opportunities], synthesis] =
    await Promise.all([
      loadCounts(supabase, job.id),
      loadSynthesisSummary(supabase, job.id),
    ]);

  return (
    <main className="max-w-5xl p-8">
      <BackLink />

      <div className="mt-2 flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-bold">{job.query ?? "Research job"}</h1>
        <StatusBadge status={job.status} />
      </div>

      {job.status === "queued" && (
        <p className="mt-3 rounded border border-foreground/15 bg-foreground/5 p-3 text-sm">
          This research is queued. The research engine is not connected to the
          dashboard yet, so nothing will be collected until it is.
        </p>
      )}
      {job.status === "failed" && (
        <p className="mt-3 rounded border border-red-500/40 p-3 text-sm text-red-600">
          This research job failed. Anything collected before the failure is
          shown below and may be incomplete.
        </p>
      )}

      <Section
        title="Configuration"
        description="What you asked the system to research."
      >
        <dl className="grid gap-x-8 gap-y-3 text-sm md:grid-cols-[8rem_1fr]">
          <dt className="text-foreground/60">Audience</dt>
          <dd>{job.audience ?? "—"}</dd>
          <dt className="text-foreground/60">Objective</dt>
          <dd>{job.objective ?? "—"}</dd>
          <dt className="text-foreground/60">Created</dt>
          <dd>{formatDate(job.created_at)}</dd>
          <dt className="text-foreground/60">Started</dt>
          <dd>{formatDate(job.started_at)}</dd>
          <dt className="text-foreground/60">Completed</dt>
          <dd>{formatDate(job.completed_at)}</dd>
        </dl>
        <p className="mt-3 text-xs text-foreground/60">
          Platform, depth and filters chosen when starting research are not
          stored yet.
        </p>
      </Section>

      <Section title="Collection" description="What has been gathered.">
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat
            label="Videos"
            count={videos}
            href={`/research/content?job=${job.id}`}
          />
          <Stat
            label="Comments"
            count={comments}
            href={`/research/comments?job=${job.id}`}
          />
          <Stat label="Transcripts" count={transcripts} />
        </div>
      </Section>

      <Section
        title="Analysis"
        description="Content in this job with at least one classification interpretation."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat label="Analysed content" count={analysed} />
        </div>
        {analysed.value === 0 && (
          <p className="mt-2 text-sm text-foreground/60">
            No analysis has been run for this job yet.
          </p>
        )}
      </Section>

      <Section
        title="Insights"
        description="What the synthesis found across this job's evidence."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat
            label="Insights (latest synthesis)"
            count={synthesis.insights}
            href={insightsHref(job.id)}
          />
        </div>
        {synthesis.error ? null : synthesis.latest ? (
          <p className="mt-2 text-sm text-foreground/70">
            Latest synthesis: {synthesis.latest.model} · {synthesis.latest.promptVersion} ·{" "}
            {formatDate(synthesis.latest.createdAt)}
            {synthesis.runs > 1 ? ` (${synthesis.runs} runs)` : ""}.{" "}
            {synthesis.insights.value === 0 ? "It completed and found no insights. " : ""}
            <Link href={insightsHref(job.id)} className="underline">
              View insights and their evidence
            </Link>
          </p>
        ) : (
          <p className="mt-2 text-sm text-foreground/60">
            No synthesis has been run for this job yet.
          </p>
        )}
      </Section>

      <Section
        title="Opportunities"
        description="What could become useful content."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat label="Opportunities" count={opportunities} />
        </div>
      </Section>

      <nav
        aria-label="Browse collected data"
        className="mt-10 flex flex-wrap gap-4 border-t border-foreground/15 pt-4 text-sm"
      >
        <span className="text-foreground/60">
          Browse all (not filtered to this job):
        </span>
        <Link href="/research/content" className="underline">
          Content
        </Link>
        <Link href="/research/comments" className="underline">
          Comments
        </Link>
        <Link href="/research/insights" className="underline">
          Insights
        </Link>
        <Link href="/research/opportunities" className="underline">
          Opportunities
        </Link>
      </nav>
    </main>
  );
}
