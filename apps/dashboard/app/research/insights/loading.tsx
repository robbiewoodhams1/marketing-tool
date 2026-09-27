export default function Loading() {
  return (
    <main className="max-w-5xl p-8" aria-busy aria-label="Loading insights">
      <div className="h-6 w-40 animate-pulse rounded bg-foreground/10" />
      <div className="mt-6 h-28 animate-pulse rounded bg-foreground/5" />
      <div className="mt-6 space-y-6">
        {[0, 1].map((i) => (
          <div key={i} className="h-56 animate-pulse rounded bg-foreground/5" />
        ))}
      </div>
    </main>
  );
}
