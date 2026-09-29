import { AlertTriangle, MessageSquare } from "lucide-react";
import { createClient } from "@/supabase/server";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  JobScopeBanner,
  JobScopeProblem,
  resolveJobScope,
} from "../_lib/job-scope";

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

type SearchParams = {
  job?: string | string[];
  q?: string | string[];
  type?: string | string[];
};

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

export default async function CommentsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const supabase = await createClient();
  const scope = await resolveJobScope(supabase, params.job);
  const q = first(params.q).trim();
  const type = first(params.type);

  if (scope.state === "not-found" || scope.state === "error") {
    return (
      <main className="flex-1 p-6">
        <h1 className="text-2xl font-semibold tracking-tight">Comments</h1>
        <JobScopeProblem scope={scope} />
      </main>
    );
  }

  const columns = "id, text, likes, type, created_at";
  // Comments have no research_job_id: they belong to a job through their
  // content row (research_jobs -> content -> comments), so join on content.
  let query =
    scope.state === "ok"
      ? supabase.from("comments").select(`${columns}, content!inner(research_job_id)`).eq("content.research_job_id", scope.id)
      : supabase.from("comments").select(columns);
  if (q) query = query.ilike("text", `%${q}%`);
  if (type && type !== "all") query = query.eq("type", type);
  const { data: comments, error } = await query.order("created_at", { ascending: false }).limit(200);

  const { data: typeRows } = await supabase.from("comments").select("type").not("type", "is", null);
  const types = [...new Set((typeRows ?? []).map((r) => r.type).filter(Boolean))] as string[];

  return (
    <main className="flex-1 space-y-4 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Comments</h1>
        <p className="mt-1 text-muted-foreground">Audience intelligence: what people actually said.</p>
      </div>
      {scope.state === "ok" && <JobScopeBanner scope={scope} noun="Comments" />}

      <form className="flex flex-wrap items-end gap-3">
        {scope.state === "ok" && <input type="hidden" name="job" value={scope.id} />}
        <div className="min-w-52 flex-1">
          <Input name="q" defaultValue={q} placeholder="Search comment text…" />
        </div>
        <Select name="type" defaultValue={type || "all"}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All types</SelectItem>
            {types.map((t) => (
              <SelectItem key={t} value={t}>
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button type="submit" variant="secondary">
          Filter
        </Button>
      </form>

      {error && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Error loading comments</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}

      {!error && comments?.length === 0 && (
        <Empty>
          <EmptyMedia variant="icon">
            <MessageSquare />
          </EmptyMedia>
          <EmptyTitle>No comments found</EmptyTitle>
          <EmptyDescription>
            {q || type
              ? "Nothing matches this search/filter. Try clearing it."
              : scope.state === "ok"
                ? "No comments have been collected for this research job yet."
                : "No comments yet."}
          </EmptyDescription>
        </Empty>
      )}

      {!error && comments && comments.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {comments.map((comment) => (
            <div key={comment.id} className="flex flex-col gap-2 rounded-lg border p-4 text-sm">
              <p className="flex-1">{comment.text ?? "—"}</p>
              <div className="flex flex-wrap items-center gap-2">
                {comment.type && <Badge variant="outline">{comment.type}</Badge>}
                {comment.likes !== null && <Badge variant="secondary">{comment.likes} likes</Badge>}
                <span className="ml-auto text-xs text-muted-foreground">{formatDate(comment.created_at)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
