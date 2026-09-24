import Link from "next/link";
import { createClient } from "@/supabase/server";
import { formatDate } from "../_lib/format";
import { StatusBadge } from "./status-badge";

const RECENT_LIMIT = 20;

export async function RecentResearch() {
  const supabase = await createClient();
  const { data: jobs, error } = await supabase
    .from("research_jobs")
    .select("id, query, audience, status, created_at, completed_at")
    .order("created_at", { ascending: false })
    .limit(RECENT_LIMIT);

  if (error) {
    return (
      <p role="alert" className="mt-4 text-red-600">
        Error loading recent research: {error.message}
      </p>
    );
  }

  if (!jobs || jobs.length === 0) {
    return (
      <div className="mt-4 rounded border border-dashed border-foreground/25 p-6 text-sm">
        <p className="font-medium">No research yet.</p>
        <p className="mt-1 text-foreground/70">
          Create your first research job above to start investigating your
          market.
        </p>
      </div>
    );
  }

  return (
    <ul className="mt-4 divide-y divide-foreground/10 rounded border border-foreground/15">
      {jobs.map((job) => (
        <li key={job.id}>
          <Link
            href={`/research/${job.id}`}
            className="grid gap-1 px-4 py-3 hover:bg-foreground/5 md:grid-cols-[1fr_9rem_15rem] md:items-center md:gap-4"
          >
            <span>
              <span className="block font-medium">{job.query ?? "Untitled"}</span>
              <span className="block text-sm text-foreground/60">
                {job.audience ?? "No audience set"}
              </span>
            </span>
            <StatusBadge status={job.status} />
            <span className="text-sm text-foreground/60">
              Created {formatDate(job.created_at)}
              {job.completed_at && (
                <span className="block">
                  Completed {formatDate(job.completed_at)}
                </span>
              )}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function RecentResearchSkeleton() {
  return (
    <div
      aria-busy
      aria-label="Loading recent research"
      className="mt-4 space-y-px overflow-hidden rounded border border-foreground/15"
    >
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-14 animate-pulse bg-foreground/5" />
      ))}
    </div>
  );
}
