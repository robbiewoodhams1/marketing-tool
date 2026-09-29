import Link from "next/link";
import {
  ArrowRight,
  CircleCheck,
  CircleX,
  Clapperboard,
  FileText,
  Image as ImageIcon,
  Lightbulb,
  Sparkles,
} from "lucide-react";
import { PipelineStrip } from "@/components/pipeline-strip";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Button } from "@/components/ui/button";
import { formatRelativeTime } from "@/lib/format";
import { createClient } from "@/supabase/server";
import {
  type ActivityItem,
  loadCurrentWork,
  loadPipelineCounts,
  loadRecentActivity,
} from "./_lib/overview-data";

const ACTIVITY_ICON: Record<ActivityItem["kind"], typeof FileText> = {
  research_job_completed: FileText,
  synthesis_created: Lightbulb,
  opportunity_run_created: Sparkles,
  production_run_created: Clapperboard,
  media_asset_generated: ImageIcon,
};

function ActivityRow({ item }: { item: ActivityItem }) {
  const Icon = ACTIVITY_ICON[item.kind];
  const failed = item.kind === "media_asset_generated" && item.label.includes("failed");
  return (
    <Link
      href={item.href}
      className="flex items-start gap-3 rounded-md px-2 py-2 text-sm hover:bg-muted"
    >
      <Icon className={`mt-0.5 size-4 shrink-0 ${failed ? "text-destructive" : "text-muted-foreground"}`} />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{item.label}</p>
        {item.detail && <p className="truncate text-muted-foreground">{item.detail}</p>}
      </div>
      <span className="shrink-0 text-xs text-muted-foreground">{formatRelativeTime(item.at)}</span>
    </Link>
  );
}

export default async function OverviewPage() {
  const supabase = await createClient();
  const [counts, activity, current, jobCheck] = await Promise.all([
    loadPipelineCounts(supabase),
    loadRecentActivity(supabase),
    loadCurrentWork(supabase),
    supabase.from("research_jobs").select("id", { count: "exact", head: true }),
  ]);

  const currentWorkItems = [
    current.researchJob,
    current.synthesis,
    current.opportunityRun,
    current.productionRun,
    current.mediaRun,
  ].filter((item): item is NonNullable<typeof item> => item !== null);

  return (
    <main className="flex-1 space-y-8 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Marketing Organisation</h1>
          <p className="mt-1 max-w-2xl text-muted-foreground">
            Research, discover opportunities, produce content and learn from performance.
          </p>
        </div>
        <Button render={<Link href="/research" />} nativeButton={false}>
          Start research
          <ArrowRight data-icon="inline-end" />
        </Button>
      </div>

      <section>
        <h2 className="mb-3 text-sm font-medium text-muted-foreground">Pipeline</h2>
        <PipelineStrip counts={counts} />
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
            <CardDescription>What the organisation has done most recently.</CardDescription>
          </CardHeader>
          <CardContent>
            {activity.length === 0 ? (
              <Empty>
                <EmptyMedia variant="icon">
                  <Sparkles />
                </EmptyMedia>
                <EmptyTitle>No activity yet</EmptyTitle>
                <EmptyDescription>
                  Start a research job to begin the Research → Insights → Opportunities →
                  Production → Media loop.
                </EmptyDescription>
              </Empty>
            ) : (
              <div className="-mx-2 flex flex-col gap-0.5">
                {activity.map((item) => (
                  <ActivityRow key={`${item.kind}-${item.href}-${item.at}`} item={item} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>System status</CardTitle>
            <CardDescription>What is actually true right now.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Database</span>
              {jobCheck.error ? (
                <Badge variant="destructive">
                  <CircleX data-icon="inline-start" />
                  Error
                </Badge>
              ) : (
                <Badge variant="secondary">
                  <CircleCheck data-icon="inline-start" />
                  Connected
                </Badge>
              )}
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Research jobs</span>
              <span className="font-medium">{jobCheck.count ?? "—"}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Last activity</span>
              <span className="font-medium">
                {activity[0] ? formatRelativeTime(activity[0].at) : "—"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Media provider</span>
              <span className="font-medium">Gemini (image only)</span>
            </div>
          </CardContent>
        </Card>
      </div>

      <section>
        <h2 className="mb-3 text-sm font-medium text-muted-foreground">Current work</h2>
        {currentWorkItems.length === 0 ? (
          <Empty>
            <EmptyMedia variant="icon">
              <FileText />
            </EmptyMedia>
            <EmptyTitle>Nothing in progress</EmptyTitle>
            <EmptyDescription>Nothing has been created yet.</EmptyDescription>
          </Empty>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {currentWorkItems.map((item) => (
              <Link key={item.label} href={item.href}>
                <Card className="h-full transition-colors hover:bg-muted/50">
                  <CardHeader>
                    <CardDescription>{item.label}</CardDescription>
                    <CardTitle className="truncate text-base">{item.title}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <p className="text-xs text-muted-foreground">
                      {item.meta ? `${item.meta} · ` : ""}
                      {formatRelativeTime(item.at)}
                    </p>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
