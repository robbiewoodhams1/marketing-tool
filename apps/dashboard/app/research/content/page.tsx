import { AlertTriangle, Boxes } from "lucide-react";
import { createClient } from "@/supabase/server";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  classificationView,
  describeField,
  describeProvenance,
  type ClassificationView,
} from "../_lib/classification";
import {
  JobScopeBanner,
  JobScopeProblem,
  resolveJobScope,
} from "../_lib/job-scope";

function formatNumber(value: number | null) {
  return value === null ? "—" : value.toLocaleString();
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

// The derived cells for one row. Nothing here reads raw content columns.
function ClassificationCells({ view }: { view: ClassificationView }) {
  if (view.kind !== "ok") {
    return (
      <>
        <TableCell colSpan={5} className="text-muted-foreground">
          {view.kind === "none" ? "Not classified" : "Classification unreadable (malformed result)"}
        </TableCell>
        <TableCell className="text-xs">
          <Source view={view} />
        </TableCell>
      </>
    );
  }
  const { fields } = view;
  return (
    <>
      {(["topic", "pain_point", "hook", "hook_type", "format"] as const).map((name) => (
        <TableCell key={name}>
          {describeField(fields[name]) ? (
            <Tooltip>
              <TooltipTrigger className="cursor-default">{fields[name].value ?? "—"}</TooltipTrigger>
              <TooltipContent>{describeField(fields[name])}</TooltipContent>
            </Tooltip>
          ) : (
            (fields[name].value ?? "—")
          )}
        </TableCell>
      ))}
      <TableCell className="text-xs">
        <Source view={view} />
      </TableCell>
    </>
  );
}

function Source({ view }: { view: ClassificationView }) {
  if (view.kind === "none") return <>—</>;
  const { provenance, earlier } = view;
  return (
    <>
      <span>{describeProvenance(provenance)}</span>
      <br />
      <span className="text-muted-foreground">
        {formatDate(provenance.createdAt)}
        {earlier > 0 ? ` · +${earlier} earlier` : ""}
      </span>
    </>
  );
}

export default async function ContentPage({
  searchParams,
}: {
  searchParams: Promise<{ job?: string | string[] }>;
}) {
  const supabase = await createClient();
  const scope = await resolveJobScope(supabase, (await searchParams).job);

  if (scope.state === "not-found" || scope.state === "error") {
    return (
      <main className="flex-1 p-6">
        <h1 className="text-2xl font-semibold tracking-tight">Content</h1>
        <JobScopeProblem scope={scope} />
      </main>
    );
  }

  let query = supabase
    .from("content")
    .select(
      "id, research_job_id, platform, url, title, creator, published_at, views, likes, comments_count, created_at, interpretations(id, analysis_type, model, prompt_version, schema_version, result, created_at)",
    )
    .eq("interpretations.analysis_type", "classification")
    .order("created_at", { ascending: false })
    .order("created_at", { ascending: false, referencedTable: "interpretations" });
  if (scope.state === "ok") query = query.eq("research_job_id", scope.id);
  const { data: content, error } = await query;

  return (
    <main className="flex-1 space-y-4 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Content</h1>
        <p className="mt-1 text-muted-foreground">
          Research evidence collected from the platforms you searched.
        </p>
      </div>
      {scope.state === "ok" && <JobScopeBanner scope={scope} noun="Content" />}

      {error && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Error loading content</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}

      {!error && content?.length === 0 && (
        <Empty>
          <EmptyMedia variant="icon">
            <Boxes />
          </EmptyMedia>
          <EmptyTitle>No content yet</EmptyTitle>
          <EmptyDescription>
            {scope.state === "ok"
              ? "No content has been collected for this research job yet."
              : "Start a research job to begin collecting content."}
          </EmptyDescription>
        </Empty>
      )}

      {!error && content && content.length > 0 && (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead colSpan={7}>Raw content</TableHead>
                <TableHead colSpan={6} className="bg-muted/50">
                  Classification <span className="font-normal text-muted-foreground">(derived)</span>
                </TableHead>
              </TableRow>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Platform</TableHead>
                <TableHead>Creator</TableHead>
                <TableHead>Published</TableHead>
                <TableHead className="text-right">Views</TableHead>
                <TableHead className="text-right">Likes</TableHead>
                <TableHead className="text-right">Comments</TableHead>
                <TableHead className="bg-muted/50">Topic</TableHead>
                <TableHead className="bg-muted/50">Pain point</TableHead>
                <TableHead className="bg-muted/50">Hook</TableHead>
                <TableHead className="bg-muted/50">Hook type</TableHead>
                <TableHead className="bg-muted/50">Format</TableHead>
                <TableHead className="bg-muted/50">Model · prompt</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {content.map((item) => (
                <TableRow key={item.id}>
                  <TableCell className="max-w-xs truncate">
                    {item.url ? (
                      <a href={item.url} target="_blank" rel="noopener noreferrer" className="underline">
                        {item.title ?? item.url}
                      </a>
                    ) : (
                      (item.title ?? "—")
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{item.platform ?? "—"}</Badge>
                  </TableCell>
                  <TableCell>{item.creator ?? "—"}</TableCell>
                  <TableCell>{formatDate(item.published_at)}</TableCell>
                  <TableCell className="text-right">{formatNumber(item.views)}</TableCell>
                  <TableCell className="text-right">{formatNumber(item.likes)}</TableCell>
                  <TableCell className="text-right">{formatNumber(item.comments_count)}</TableCell>
                  <ClassificationCells view={classificationView(item.interpretations)} />
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </main>
  );
}
