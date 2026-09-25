import { createClient } from "@/supabase/server";
import {
  JobScopeBanner,
  JobScopeProblem,
  resolveJobScope,
} from "../_lib/job-scope";

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

export default async function CommentsPage({
  searchParams,
}: {
  searchParams: Promise<{ job?: string | string[] }>;
}) {
  const supabase = await createClient();
  const scope = await resolveJobScope(supabase, (await searchParams).job);

  if (scope.state === "not-found" || scope.state === "error") {
    return (
      <main className="p-8">
        <h1 className="text-xl font-bold">Comments</h1>
        <JobScopeProblem scope={scope} />
      </main>
    );
  }

  const columns =
    "id, text, likes, type, topic, pain_point, desire, objection, created_at";
  // Comments have no research_job_id: they belong to a job through their
  // content row (research_jobs -> content -> comments), so join on content.
  const { data: comments, error } =
    scope.state === "ok"
      ? await supabase
          .from("comments")
          .select(`${columns}, content!inner(research_job_id)`)
          .eq("content.research_job_id", scope.id)
          .order("created_at", { ascending: false })
      : await supabase
          .from("comments")
          .select(columns)
          .order("created_at", { ascending: false });

  return (
    <main className="p-8">
      <h1 className="text-xl font-bold">Comments</h1>
      {scope.state === "ok" && (
        <JobScopeBanner scope={scope} noun="Comments" />
      )}

      {error && (
        <p className="mt-4 text-red-600">
          Error loading comments: {error.message}
        </p>
      )}

      {!error && comments?.length === 0 && (
        <p className="mt-4">
          {scope.state === "ok"
            ? "No comments have been collected for this research job yet."
            : "No comments yet."}
        </p>
      )}

      {!error && comments && comments.length > 0 && (
        <table className="mt-4 border-collapse text-sm">
          <thead>
            <tr>
              <th className="border px-2 py-1 text-left">Comment</th>
              <th className="border px-2 py-1 text-right">Likes</th>
              <th className="border px-2 py-1 text-left">Type</th>
              <th className="border px-2 py-1 text-left">Topic</th>
              <th className="border px-2 py-1 text-left">Pain point</th>
              <th className="border px-2 py-1 text-left">Desire</th>
              <th className="border px-2 py-1 text-left">Objection</th>
              <th className="border px-2 py-1 text-left">Created</th>
            </tr>
          </thead>
          <tbody>
            {comments.map((comment) => (
              <tr key={comment.id}>
                <td className="border px-2 py-1">{comment.text ?? "—"}</td>
                <td className="border px-2 py-1 text-right">
                  {comment.likes ?? "—"}
                </td>
                <td className="border px-2 py-1">{comment.type ?? "—"}</td>
                <td className="border px-2 py-1">{comment.topic ?? "—"}</td>
                <td className="border px-2 py-1">
                  {comment.pain_point ?? "—"}
                </td>
                <td className="border px-2 py-1">{comment.desire ?? "—"}</td>
                <td className="border px-2 py-1">
                  {comment.objection ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {formatDate(comment.created_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
