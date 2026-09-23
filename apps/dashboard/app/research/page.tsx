import Link from "next/link";
import { createClient } from "@/supabase/server";

export default async function ResearchPage() {
  const supabase = await createClient();

  const { data: jobs, error } = await supabase
    .from("research_jobs")
    .select("id, query, audience, objective, status, created_at")
    .order("created_at", { ascending: false });

  return (
    <main className="p-8">
      <h1 className="text-xl font-bold">Research</h1>

      <nav className="mt-2 text-sm flex gap-4">
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

      {error && (
        <p className="mt-4 text-red-600">
          Error loading research jobs: {error.message}
        </p>
      )}

      {!error && jobs?.length === 0 && (
        <p className="mt-4">No research jobs yet.</p>
      )}

      {!error && jobs && jobs.length > 0 && (
        <table className="mt-4 border-collapse">
          <thead>
            <tr>
              <th className="border px-2 py-1 text-left">Query</th>
              <th className="border px-2 py-1 text-left">Audience</th>
              <th className="border px-2 py-1 text-left">Objective</th>
              <th className="border px-2 py-1 text-left">Status</th>
              <th className="border px-2 py-1 text-left">Created</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td className="border px-2 py-1">
                  <Link href={`/research/${job.id}`} className="underline">
                    {job.query}
                  </Link>
                </td>
                <td className="border px-2 py-1">{job.audience}</td>
                <td className="border px-2 py-1">{job.objective}</td>
                <td className="border px-2 py-1">{job.status}</td>
                <td className="border px-2 py-1">
                  {new Date(job.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
