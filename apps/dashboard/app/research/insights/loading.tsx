import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <main className="max-w-5xl flex-1 p-6" aria-busy aria-label="Loading insights">
      <Skeleton className="h-8 w-40" />
      <Skeleton className="mt-6 h-28 w-full" />
      <div className="mt-6 space-y-6">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-56 w-full" />
        ))}
      </div>
    </main>
  );
}
