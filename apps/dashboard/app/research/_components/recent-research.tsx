import Link from "next/link";
import { createClient } from "@/supabase/server";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { FileText } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
      <p role="alert" className="mt-4 text-sm text-destructive">
        Error loading recent research: {error.message}
      </p>
    );
  }

  if (!jobs || jobs.length === 0) {
    return (
      <Empty className="mt-4 border border-dashed">
        <EmptyMedia variant="icon">
          <FileText />
        </EmptyMedia>
        <EmptyTitle>No research yet</EmptyTitle>
        <EmptyDescription>
          Create your first research job above to start investigating your market.
        </EmptyDescription>
      </Empty>
    );
  }

  return (
    <Table className="mt-4">
      <TableHeader>
        <TableRow>
          <TableHead>Query</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Created</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {jobs.map((job) => (
          <TableRow key={job.id} className="cursor-pointer">
            <TableCell className="p-0">
              <Link href={`/research/${job.id}`} className="block px-4 py-3">
                <span className="block font-medium">{job.query ?? "Untitled"}</span>
                <span className="block text-sm text-muted-foreground">
                  {job.audience ?? "No audience set"}
                </span>
              </Link>
            </TableCell>
            <TableCell>
              <Link href={`/research/${job.id}`} className="block px-4 py-3">
                <StatusBadge status={job.status} />
              </Link>
            </TableCell>
            <TableCell className="p-0 text-sm text-muted-foreground">
              <Link href={`/research/${job.id}`} className="block px-4 py-3">
                {formatDate(job.created_at)}
                {job.completed_at && <span className="block">Completed {formatDate(job.completed_at)}</span>}
              </Link>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function RecentResearchSkeleton() {
  return (
    <div aria-busy aria-label="Loading recent research" className="mt-4 space-y-2">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-14 w-full" />
      ))}
    </div>
  );
}
