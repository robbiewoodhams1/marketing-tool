import Link from "next/link";
import { createClient } from "@/supabase/server";

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
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
        <p className="mt-4">
          <Link href="/research" className="underline">
            Back to Research
          </Link>
        </p>
      </main>
    );
  }

  if (error || !job) {
    return (
      <main className="p-8">
        <p className="text-red-600">
          Error loading research job: {error?.message}
        </p>
        <p className="mt-4">
          <Link href="/research" className="underline">
            Back to Research
          </Link>
        </p>
      </main>
    );
  }

  const { data: jobContent } = await supabase
    .from("content")
    .select("id")
    .eq("research_job_id", job.id);

  const contentIds = jobContent?.map((row) => row.id) ?? [];
  const contentCount = contentIds.length;

  let commentsCount = 0;
  if (contentIds.length > 0) {
    const { count } = await supabase
      .from("comments")
      .select("id", { count: "exact", head: true })
      .in("content_id", contentIds);
    commentsCount = count ?? 0;
  }

  return (
    <main className="p-8">
      <p>
        <Link href="/research" className="underline">
          Back to Research
        </Link>
      </p>

      <h1 className="mt-2 text-xl font-bold">{job.query ?? "Research job"}</h1>

      <table className="mt-4 border-collapse text-sm">
        <tbody>
          <tr>
            <th className="border px-2 py-1 text-left">Query</th>
            <td className="border px-2 py-1">{job.query ?? "—"}</td>
          </tr>
          <tr>
            <th className="border px-2 py-1 text-left">Audience</th>
            <td className="border px-2 py-1">{job.audience ?? "—"}</td>
          </tr>
          <tr>
            <th className="border px-2 py-1 text-left">Objective</th>
            <td className="border px-2 py-1">{job.objective ?? "—"}</td>
          </tr>
          <tr>
            <th className="border px-2 py-1 text-left">Status</th>
            <td className="border px-2 py-1">{job.status ?? "—"}</td>
          </tr>
          <tr>
            <th className="border px-2 py-1 text-left">Started</th>
            <td className="border px-2 py-1">
              {formatDate(job.started_at)}
            </td>
          </tr>
          <tr>
            <th className="border px-2 py-1 text-left">Completed</th>
            <td className="border px-2 py-1">
              {formatDate(job.completed_at)}
            </td>
          </tr>
          <tr>
            <th className="border px-2 py-1 text-left">Created</th>
            <td className="border px-2 py-1">
              {formatDate(job.created_at)}
            </td>
          </tr>
        </tbody>
      </table>

      <h2 className="mt-6 text-sm font-bold">Associated data</h2>
      <table className="mt-2 border-collapse text-sm">
        <tbody>
          <tr>
            <th className="border px-2 py-1 text-left">Content</th>
            <td className="border px-2 py-1">{contentCount}</td>
          </tr>
          <tr>
            <th className="border px-2 py-1 text-left">Comments</th>
            <td className="border px-2 py-1">{commentsCount}</td>
          </tr>
        </tbody>
      </table>

      <nav className="mt-4 text-sm flex gap-4">
        <Link href="/research/content" className="underline">
          View all Content
        </Link>
        <Link href="/research/comments" className="underline">
          View all Comments
        </Link>
        <Link href="/research/insights" className="underline">
          View all Insights
        </Link>
        <Link href="/research/opportunities" className="underline">
          View all Opportunities
        </Link>
      </nav>
    </main>
  );
}
