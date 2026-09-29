import { Suspense } from "react";
import {
  RecentResearch,
  RecentResearchSkeleton,
} from "./_components/recent-research";
import { ResearchControl } from "./_components/research-control";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function ResearchPage() {
  return (
    <main className="flex-1 space-y-8 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Research</h1>
        <p className="mt-1 text-muted-foreground">
          Start a new research job, or pick up an existing one below.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Start research</CardTitle>
          <CardDescription>What do you want to learn about your market?</CardDescription>
        </CardHeader>
        <CardContent>
          <ResearchControl />
        </CardContent>
      </Card>

      <section className="max-w-5xl">
        <h2 className="text-lg font-semibold">Recent research</h2>
        <Suspense fallback={<RecentResearchSkeleton />}>
          <RecentResearch />
        </Suspense>
      </section>
    </main>
  );
}
