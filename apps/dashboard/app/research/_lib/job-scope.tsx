import Link from "next/link";
import type { createClient } from "@/supabase/server";

type Supabase = Awaited<ReturnType<typeof createClient>>;

export type JobScope =
  | { state: "global" }
  | { state: "ok"; id: string; query: string | null }
  | { state: "not-found"; id: string }
  | { state: "error"; id: string; message: string };

// Resolves the optional `?job=` parameter. A malformed UUID (Postgres 22P02)
// is treated the same as a job that does not exist.
export async function resolveJobScope(
  supabase: Supabase,
  job: string | string[] | undefined,
): Promise<JobScope> {
  const id = Array.isArray(job) ? job[0] : job;
  if (!id) return { state: "global" };

  const { data, error } = await supabase
    .from("research_jobs")
    .select("id, query")
    .eq("id", id)
    .maybeSingle();

  if (error) {
    return error.code === "22P02"
      ? { state: "not-found", id }
      : { state: "error", id, message: error.message };
  }
  return data
    ? { state: "ok", id: data.id, query: data.query }
    : { state: "not-found", id };
}

export function JobScopeBanner({
  scope,
  noun,
}: {
  scope: Extract<JobScope, { state: "ok" }>;
  noun: string;
}) {
  return (
    <div className="mt-2 text-sm text-foreground/70">
      <p>
        {noun} from this research job:{" "}
        <span className="font-medium text-foreground">
          {scope.query ?? "Untitled research"}
        </span>
      </p>
      <p className="mt-1 flex flex-wrap gap-4">
        <Link href={`/research/${scope.id}`} className="underline">
          Back to research job
        </Link>
        <Link href={`/research/${noun.toLowerCase()}`} className="underline">
          View all {noun.toLowerCase()}
        </Link>
      </p>
    </div>
  );
}

// Shown instead of the table when the job cannot be used, so a bad ID is
// never presented as a legitimately empty job.
export function JobScopeProblem({
  scope,
}: {
  scope: Extract<JobScope, { state: "not-found" | "error" }>;
}) {
  return (
    <div className="mt-4">
      {scope.state === "not-found" ? (
        <p>Research job not found.</p>
      ) : (
        <p role="alert" className="text-red-600">
          Error loading research job: {scope.message}
        </p>
      )}
      <p className="mt-2 text-sm">
        <Link href="/research" className="underline">
          Back to Research
        </Link>
      </p>
    </div>
  );
}
