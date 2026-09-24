const STATUSES: Record<string, { label: string; dot: string }> = {
  queued: { label: "Queued", dot: "bg-foreground/40" },
  running: { label: "Running", dot: "bg-blue-500" },
  completed: { label: "Complete", dot: "bg-green-500" },
  failed: { label: "Failed", dot: "bg-red-500" },
};

export function StatusBadge({ status }: { status: string | null }) {
  const known = status ? STATUSES[status] : undefined;
  return (
    <span className="inline-flex items-center gap-2 text-sm">
      <span
        aria-hidden
        className={`h-2 w-2 rounded-full ${known?.dot ?? "bg-foreground/40"}`}
      />
      {known?.label ?? status ?? "Unknown"}
    </span>
  );
}
