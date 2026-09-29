import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { StatusBadge } from "../_components/status-badge";
import { formatDate, formatNumber } from "../_lib/format";
import {
  insightsHref,
  parseSynthesisRuns,
  selectRun,
  type SynthesisRun,
} from "../_lib/synthesis";
import { createClient } from "@/supabase/server";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { AlertTriangle, FileText, Lightbulb, Sparkles } from "lucide-react";

type Supabase = Awaited<ReturnType<typeof createClient>>;
type Count = { value: number | null; error: string | null };

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
    count(
      supabase
        .from("comments")
        .select("id, content!inner(research_job_id)", head)
        .eq("content.research_job_id", jobId),
    ),
    count(content().not("transcript", "is", null)),
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
  latest: SynthesisRun | null;
  runs: number;
  insights: Count;
  error: string | null;
};

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

// Small previews so the tabs show real data, not just a link elsewhere - the
// full tables still live at their own routes for anything beyond this.
const PREVIEW_LIMIT = 5;

async function loadContentPreview(supabase: Supabase, jobId: string) {
  return supabase
    .from("content")
    .select("id, title, creator, platform, views, url")
    .eq("research_job_id", jobId)
    .order("created_at", { ascending: false })
    .limit(PREVIEW_LIMIT);
}

async function loadCommentsPreview(supabase: Supabase, jobId: string) {
  return supabase
    .from("comments")
    .select("id, text, likes, content!inner(research_job_id)")
    .eq("content.research_job_id", jobId)
    .order("created_at", { ascending: false })
    .limit(PREVIEW_LIMIT);
}

function Stat({ label, count, href }: { label: string; count: Count; href?: string }) {
  const card = (
    <Card className={href ? "transition-colors hover:bg-muted/50" : undefined}>
      <CardContent>
        <p className="text-2xl font-semibold tabular-nums">
          {count.error ? "—" : formatNumber(count.value)}
        </p>
        <p className="text-sm text-muted-foreground">{label}</p>
        {count.error && (
          <p role="alert" className="mt-1 text-xs text-destructive">
            Error: {count.error}
          </p>
        )}
      </CardContent>
    </Card>
  );
  return href ? <Link href={href}>{card}</Link> : card;
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
    .select("id, query, audience, objective, status, started_at, completed_at, created_at")
    .eq("id", id)
    .maybeSingle();

  const notFound = !error && !job;
  const isInvalidId = error?.code === "22P02";

  if (notFound || isInvalidId) {
    return (
      <main className="flex-1 p-6">
        <Empty>
          <EmptyMedia variant="icon">
            <FileText />
          </EmptyMedia>
          <EmptyTitle>Research job not found</EmptyTitle>
          <EmptyDescription>
            <Button variant="link" render={<Link href="/research" />} nativeButton={false} className="p-0">
              Back to Research
            </Button>
          </EmptyDescription>
        </Empty>
      </main>
    );
  }

  if (error || !job) {
    return (
      <main className="flex-1 p-6">
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Error loading research job</AlertTitle>
          <AlertDescription>{error?.message}</AlertDescription>
        </Alert>
      </main>
    );
  }

  const [[content, comments, transcripts, analysed, opportunities], synthesis, contentPreview, commentsPreview] =
    await Promise.all([
      loadCounts(supabase, job.id),
      loadSynthesisSummary(supabase, job.id),
      loadContentPreview(supabase, job.id),
      loadCommentsPreview(supabase, job.id),
    ]);

  return (
    <main className="flex-1 space-y-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{job.query ?? "Research job"}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {job.audience ?? "No audience set"}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>

      {job.status === "queued" && (
        <Alert>
          <AlertDescription>
            This research is queued. The research engine is not connected to the dashboard yet, so
            nothing will be collected until it is.
          </AlertDescription>
        </Alert>
      )}
      {job.status === "failed" && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertDescription>
            This research job failed. Anything collected before the failure is shown below and may
            be incomplete.
          </AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
          <CardDescription>What you asked the system to research.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-8 gap-y-3 text-sm md:grid-cols-[8rem_1fr]">
            <dt className="text-muted-foreground">Audience</dt>
            <dd>{job.audience ?? "—"}</dd>
            <dt className="text-muted-foreground">Objective</dt>
            <dd>{job.objective ?? "—"}</dd>
            <dt className="text-muted-foreground">Created</dt>
            <dd>{formatDate(job.created_at)}</dd>
            <dt className="text-muted-foreground">Started</dt>
            <dd>{formatDate(job.started_at)}</dd>
            <dt className="text-muted-foreground">Completed</dt>
            <dd>{formatDate(job.completed_at)}</dd>
          </dl>
        </CardContent>
      </Card>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="content">Content</TabsTrigger>
          <TabsTrigger value="comments">Comments</TabsTrigger>
          <TabsTrigger value="synthesis">Synthesis</TabsTrigger>
          <TabsTrigger value="opportunities">Opportunities</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Content" count={content} href={`/research/content?job=${job.id}`} />
            <Stat label="Comments" count={comments} href={`/research/comments?job=${job.id}`} />
            <Stat label="Transcripts" count={transcripts} />
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Analysed content" count={analysed} />
            <Stat label="Insights (latest synthesis)" count={synthesis.insights} href={insightsHref(job.id)} />
            <Stat label="Opportunities" count={opportunities} />
          </div>
        </TabsContent>

        <TabsContent value="content">
          <Card>
            <CardHeader>
              <CardTitle>Content</CardTitle>
              <CardDescription>Most recently collected, {formatNumber(content.value)} total.</CardDescription>
            </CardHeader>
            <CardContent>
              {contentPreview.data && contentPreview.data.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Title</TableHead>
                      <TableHead>Platform</TableHead>
                      <TableHead className="text-right">Views</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {contentPreview.data.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell className="max-w-xs truncate">
                          {c.url ? (
                            <a href={c.url} target="_blank" rel="noopener noreferrer" className="underline">
                              {c.title ?? c.url}
                            </a>
                          ) : (
                            (c.title ?? "—")
                          )}
                        </TableCell>
                        <TableCell>{c.platform ?? "—"}</TableCell>
                        <TableCell className="text-right">{formatNumber(c.views)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="text-sm text-muted-foreground">No content collected yet.</p>
              )}
            </CardContent>
            <div className="px-6 pb-6">
              <Button variant="link" className="px-0" render={<Link href={`/research/content?job=${job.id}`} />} nativeButton={false}>
                View all content
                <ArrowRight data-icon="inline-end" />
              </Button>
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="comments">
          <Card>
            <CardHeader>
              <CardTitle>Comments</CardTitle>
              <CardDescription>Most recently collected, {formatNumber(comments.value)} total.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {commentsPreview.data && commentsPreview.data.length > 0 ? (
                commentsPreview.data.map((c) => (
                  <div key={c.id} className="rounded-md border p-3 text-sm">
                    <p>{c.text ?? "—"}</p>
                    {c.likes !== null && (
                      <Badge variant="secondary" className="mt-2">
                        {c.likes} likes
                      </Badge>
                    )}
                  </div>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">No comments collected yet.</p>
              )}
            </CardContent>
            <div className="px-6 pb-6">
              <Button variant="link" className="px-0" render={<Link href={`/research/comments?job=${job.id}`} />} nativeButton={false}>
                View all comments
                <ArrowRight data-icon="inline-end" />
              </Button>
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="synthesis">
          <Card>
            <CardHeader>
              <CardTitle>Synthesis</CardTitle>
              <CardDescription>What the synthesis found across this job&apos;s evidence.</CardDescription>
            </CardHeader>
            <CardContent>
              {synthesis.error ? (
                <Alert variant="destructive">
                  <AlertDescription>{synthesis.error}</AlertDescription>
                </Alert>
              ) : synthesis.latest ? (
                <div className="space-y-3 text-sm">
                  <p>
                    <span className="font-medium">{synthesis.latest.model}</span> ·{" "}
                    {synthesis.latest.promptVersion} · {formatDate(synthesis.latest.createdAt)}
                    {synthesis.runs > 1 ? ` (${synthesis.runs} runs)` : ""}
                  </p>
                  {synthesis.insights.value === 0 && (
                    <p className="text-muted-foreground">It completed and found no insights.</p>
                  )}
                  <Button render={<Link href={insightsHref(job.id)} />} nativeButton={false}>
                    <Lightbulb data-icon="inline-start" />
                    View insights and their evidence
                  </Button>
                </div>
              ) : (
                <Empty>
                  <EmptyMedia variant="icon">
                    <Lightbulb />
                  </EmptyMedia>
                  <EmptyTitle>No synthesis yet</EmptyTitle>
                  <EmptyDescription>
                    No synthesis has been run for this job yet.
                  </EmptyDescription>
                </Empty>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="opportunities">
          <Card>
            <CardHeader>
              <CardTitle>Opportunities</CardTitle>
              <CardDescription>What could become useful content.</CardDescription>
            </CardHeader>
            <CardContent>
              {opportunities.value === 0 ? (
                <Empty>
                  <EmptyMedia variant="icon">
                    <Sparkles />
                  </EmptyMedia>
                  <EmptyTitle>No opportunities yet</EmptyTitle>
                  <EmptyDescription>
                    Opportunities are created from this job&apos;s synthesis once it has insights.
                  </EmptyDescription>
                </Empty>
              ) : (
                <Button render={<Link href="/research/opportunities" />} nativeButton={false}>
                  <Sparkles data-icon="inline-start" />
                  View {formatNumber(opportunities.value)} opportunities
                </Button>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Separator />
      <p className="text-sm text-muted-foreground">
        Platform, depth and filters chosen when starting research are not stored yet.
      </p>
    </main>
  );
}
