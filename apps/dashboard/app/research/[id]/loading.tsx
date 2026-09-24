export default function Loading() {
  return (
    <main className="max-w-5xl p-8" aria-busy aria-label="Loading research job">
      <div className="h-6 w-64 animate-pulse rounded bg-foreground/10" />
      <div className="mt-8 h-24 animate-pulse rounded bg-foreground/5" />
      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded bg-foreground/5" />
        ))}
      </div>
    </main>
  );
}
