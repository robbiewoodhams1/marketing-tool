import Link from "next/link";
import { Suspense } from "react";
import {
  RecentResearch,
  RecentResearchSkeleton,
} from "./_components/recent-research";
import { ResearchControl } from "./_components/research-control";

export default function ResearchPage() {
  return (
    <main className="p-8">
      <h1 className="text-xl font-bold">Research</h1>

      <section className="mt-6">
        <h2 className="text-lg font-semibold">Research control</h2>
        <ResearchControl />
      </section>

      <section className="mt-12 max-w-5xl">
        <h2 className="text-lg font-semibold">Recent research</h2>
        <Suspense fallback={<RecentResearchSkeleton />}>
          <RecentResearch />
        </Suspense>
      </section>

      <nav
        aria-label="Browse all collected data"
        className="mt-8 flex flex-wrap gap-4 text-sm"
      >
        <span className="text-foreground/60">Browse everything:</span>
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
    </main>
  );
}
