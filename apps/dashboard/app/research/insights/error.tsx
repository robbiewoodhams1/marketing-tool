"use client"; // Error boundaries must be Client Components

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

// In this Next.js version the boundary receives `retry` (not `reset`).
export default function InsightsError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="max-w-5xl flex-1 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
      <Alert variant="destructive" className="mt-4">
        <AlertTriangle />
        <AlertTitle>Something went wrong while showing insights</AlertTitle>
        <AlertDescription>
          The insights and their evidence are stored safely; this is a display problem.
        </AlertDescription>
      </Alert>
      <Button variant="outline" className="mt-4" onClick={() => retry()}>
        Try again
      </Button>
    </main>
  );
}
