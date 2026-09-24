import Link from "next/link";
import { StatusBadge } from "../_components/status-badge";
import { formatDate, formatNumber } from "../_lib/format";
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
    // "Analysed" = the content row carries an analysis result.
    count(content().not("analysis_json", "is", null)),
    count(
      supabase
        .from("insights")
        .select("id", head)
        .eq("research_job_id", jobId),
    ),
    count(
      supabase
        .from("opportunities")
        .select("id", head)
        .eq("research_job_id", jobId),
    ),
  ]);
}

function Stat({ label, count }: { label: string; count: Count }) {
  return (
    <div className="rounded border border-foreground/15 p-4">
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

  const [videos, comments, transcripts, analysed, insights, opportunities] =
    await loadCounts(supabase, job.id);

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
          <Stat label="Videos" count={videos} />
          <Stat label="Comments" count={comments} />
          <Stat label="Transcripts" count={transcripts} />
        </div>
      </Section>

      <Section title="Analysis" description="What has been analysed.">
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat label="Analysed content" count={analysed} />
        </div>
        {analysed.value === 0 && (
          <p className="mt-2 text-sm text-foreground/60">
            No analysis has been run for this job yet.
          </p>
        )}
      </Section>

      <Section title="Insights" description="What the system has discovered.">
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat label="Insights" count={insights} />
        </div>
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
