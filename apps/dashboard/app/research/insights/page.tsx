import Link from "next/link";
import { createClient } from "@/supabase/server";
import { InsightCard } from "../_components/insight-card";
import { SynthesisProvenance } from "../_components/synthesis-provenance";
import { formatDate } from "../_lib/format";
import {
  JobScopeBanner,
  JobScopeProblem,
  resolveJobScope,
} from "../_lib/job-scope";
import { insightsHref, runOutcome, selectRun } from "../_lib/synthesis";
import {
  loadRecentRuns,
  loadRunsForJob,
  loadSynthesisDetail,
} from "../_lib/synthesis-data";

type Supabase = Awaited<ReturnType<typeof createClient>>;
type SearchParams = Promise<{
  job?: string | string[];
  synthesis?: string | string[];
}>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function ErrorMessage({ what, message }: { what: string; message: string }) {
  return (
    <p role="alert" className="mt-4 text-red-600">
      Error loading {what}: {message}
    </p>
  );
}

// Every synthesis run across all research jobs; each links to its job's insights.
async function AllSyntheses({ supabase }: { supabase: Supabase }) {
  const loaded = await loadRecentRuns(supabase);
  if (!loaded.ok) return <ErrorMessage what="syntheses" message={loaded.error} />;
  const runs = loaded.data;
  if (runs.length === 0) {
    return (
      <p className="mt-4">
        No synthesis has been run for any research job yet. Insights are produced by a
        synthesis run over a research job&apos;s collected evidence.
      </p>
    );
  }
  return (
    <>
      <p className="mt-2 text-sm text-foreground/70">
        Insights belong to a research job. Choose a synthesis to see its insights and the
        evidence behind them.
      </p>
      <table className="mt-4 border-collapse text-sm">
        <thead>
          <tr>
            <th className="border px-2 py-1 text-left">Research job</th>
            <th className="border px-2 py-1 text-left">Model</th>
            <th className="border px-2 py-1 text-left">Prompt</th>
            <th className="border px-2 py-1 text-left">Run</th>
            <th className="border px-2 py-1 text-right">Insights</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td className="border px-2 py-1">
                <Link href={insightsHref(run.researchJobId, run.id)} className="underline">
                  {run.researchJobQuery ?? "Untitled research"}
                </Link>
              </td>
              <td className="border px-2 py-1">{run.model}</td>
              <td className="border px-2 py-1">{run.promptVersion}</td>
              <td className="border px-2 py-1">{formatDate(run.createdAt)}</td>
              <td className="border px-2 py-1 text-right">
                {run.insightCount === null ? "—" : run.insightCount === 0 ? "none found" : run.insightCount}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

async function JobInsights({
  supabase,
  jobId,
  requested,
}: {
  supabase: Supabase;
  jobId: string;
  requested: string | undefined;
}) {
  const loaded = await loadRunsForJob(supabase, jobId);
  if (!loaded.ok) return <ErrorMessage what="syntheses" message={loaded.error} />;

  const selection = selectRun(loaded.data, requested);

  // "No synthesis has been run" is decided by the absence of a synthesis row,
  // never inferred from an empty insights query.
  if (selection.kind === "none") {
    return (
      <div className="mt-4">
        <p>No synthesis has been run for this research job.</p>
        <p className="mt-1 text-sm text-foreground/60">
          Insights appear here once a synthesis has analysed this job&apos;s content and
          comments.
        </p>
      </div>
    );
  }
  if (selection.kind === "not-found") {
    return (
      <div className="mt-4">
        <p>That synthesis run was not found for this research job.</p>
        <p className="mt-2 text-sm">
          <Link href={insightsHref(jobId)} className="underline">
            Show the latest synthesis
          </Link>
        </p>
      </div>
    );
  }

  const { run, isLatest } = selection;
  const detail = await loadSynthesisDetail(supabase, run.id);
  if (!detail.ok) {
    return (
      <>
        <SynthesisProvenance run={run} isLatest={isLatest} runs={selection.runs} />
        <ErrorMessage what="insights" message={detail.error} />
      </>
    );
  }
  const { insights, warnings } = detail.data;
  const outcome = runOutcome(run, insights.length);

  return (
    <>
      <SynthesisProvenance run={run} isLatest={isLatest} runs={selection.runs} />

      {warnings.map((w) => (
        <p key={w} role="status" className="mt-3 text-sm text-amber-700 dark:text-amber-400">
          {w}
        </p>
      ))}

      {outcome.kind === "found-nothing" ? (
        <p className="mt-6 rounded border border-foreground/15 bg-foreground/5 p-4 text-sm">
          {outcome.message}
        </p>
      ) : (
        <div className="mt-6 space-y-6">
          <p className="text-sm text-foreground/70">
            {insights.length} insight{insights.length === 1 ? "" : "s"}, in no particular
            order. Confidence is the synthesis model&apos;s own estimate.
          </p>
          {insights.map((insight) => (
            <InsightCard key={insight.id} insight={insight} />
          ))}
        </div>
      )}
    </>
  );
}

export default async function InsightsPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const supabase = await createClient();
  const scope = await resolveJobScope(supabase, params.job);

  if (scope.state === "not-found" || scope.state === "error") {
    return (
      <main className="p-8">
        <h1 className="text-xl font-bold">Insights</h1>
        <JobScopeProblem scope={scope} />
      </main>
    );
  }

  return (
    <main className="max-w-5xl p-8">
      <h1 className="text-xl font-bold">Insights</h1>
      {scope.state === "ok" ? (
        <>
          <JobScopeBanner scope={scope} noun="Insights" />
          <JobInsights supabase={supabase} jobId={scope.id} requested={first(params.synthesis)} />
        </>
      ) : (
        <AllSyntheses supabase={supabase} />
      )}
    </main>
  );
}
