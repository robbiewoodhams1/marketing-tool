"use client"; // Error boundaries must be Client Components

import { useEffect } from "react";

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
    <main className="max-w-5xl p-8">
      <h1 className="text-xl font-bold">Insights</h1>
      <p role="alert" className="mt-4 text-red-600">
        Something went wrong while showing insights.
      </p>
      <p className="mt-1 text-sm text-foreground/60">
        The insights and their evidence are stored safely; this is a display problem.
      </p>
      <button
        type="button"
        onClick={() => retry()}
        className="mt-4 rounded border border-foreground/30 px-3 py-1 text-sm hover:bg-foreground/5"
      >
        Try again
      </button>
    </main>
  );
}
