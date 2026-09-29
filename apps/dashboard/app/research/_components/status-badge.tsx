import { Badge } from "@/components/ui/badge";

// Semantic status -> Badge variant + label. Colour is used only for meaning
// (running/complete/failed/pending), never decoration.
const STATUSES: Record<string, { label: string; variant: "secondary" | "default" | "destructive" | "outline" }> = {
  queued: { label: "Queued", variant: "outline" },
  running: { label: "Running", variant: "default" },
  completed: { label: "Complete", variant: "secondary" },
  failed: { label: "Failed", variant: "destructive" },
};

export function StatusBadge({ status }: { status: string | null }) {
  const known = status ? STATUSES[status] : undefined;
  return <Badge variant={known?.variant ?? "outline"}>{known?.label ?? status ?? "Unknown"}</Badge>;
}
