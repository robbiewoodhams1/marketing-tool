import Link from "next/link";
import { Lightbulb } from "lucide-react";
import { createClient } from "@/supabase/server";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
    <Alert variant="destructive" className="mt-4">
      <AlertDescription>
        Error loading {what}: {message}
      </AlertDescription>
    </Alert>
  );
}

// Every synthesis run across all research jobs; each links to its job's insights.
async function AllSyntheses({ supabase }: { supabase: Supabase }) {
  const loaded = await loadRecentRuns(supabase);
  if (!loaded.ok) return <ErrorMessage what="syntheses" message={loaded.error} />;
  const runs = loaded.data;
  if (runs.length === 0) {
    return (
      <Empty className="mt-4">
        <EmptyMedia variant="icon">
          <Lightbulb />
        </EmptyMedia>
        <EmptyTitle>No synthesis has been run yet</EmptyTitle>
        <EmptyDescription>
          Insights are produced by a synthesis run over a research job&apos;s collected evidence.
        </EmptyDescription>
      </Empty>
    );
  }
  return (
    <>
      <p className="mt-2 text-sm text-muted-foreground">
        Insights belong to a research job. Choose a synthesis to see its insights and the evidence
        behind them.
      </p>
      <Table className="mt-4">
        <TableHeader>
          <TableRow>
            <TableHead>Research job</TableHead>
            <TableHead>Model</TableHead>
            <TableHead>Prompt</TableHead>
            <TableHead>Run</TableHead>
            <TableHead className="text-right">Insights</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => (
            <TableRow key={run.id}>
              <TableCell>
                <Link href={insightsHref(run.researchJobId, run.id)} className="underline">
                  {run.researchJobQuery ?? "Untitled research"}
                </Link>
              </TableCell>
              <TableCell>{run.model}</TableCell>
              <TableCell>{run.promptVersion}</TableCell>
              <TableCell>{formatDate(run.createdAt)}</TableCell>
              <TableCell className="text-right">
                {run.insightCount === null ? "—" : run.insightCount === 0 ? "none found" : run.insightCount}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
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

  if (selection.kind === "none") {
    return (
      <Empty className="mt-4">
        <EmptyMedia variant="icon">
          <Lightbulb />
        </EmptyMedia>
        <EmptyTitle>No synthesis has been run for this research job</EmptyTitle>
        <EmptyDescription>
          Insights appear here once a synthesis has analysed this job&apos;s content and comments.
        </EmptyDescription>
      </Empty>
    );
  }
  if (selection.kind === "not-found") {
    return (
      <div className="mt-4 text-sm">
        <p>That synthesis run was not found for this research job.</p>
        <p className="mt-2">
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
        <Alert className="mt-6">
          <AlertDescription>{outcome.message}</AlertDescription>
        </Alert>
      ) : (
        <div className="mt-6 space-y-6">
          <p className="text-sm text-muted-foreground">
            {insights.length} insight{insights.length === 1 ? "" : "s"}, in no particular order.
            Confidence is the synthesis model&apos;s own estimate.
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
      <main className="flex-1 p-6">
        <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
        <JobScopeProblem scope={scope} />
      </main>
    );
  }

  return (
    <main className="max-w-5xl flex-1 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
        <p className="mt-1 text-muted-foreground">
          What the synthesis found across your research, with the evidence behind each finding.
        </p>
      </div>
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
