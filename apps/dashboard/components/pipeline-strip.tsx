import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { PIPELINE } from "@/lib/nav";
import type { PipelineCounts } from "@/app/_lib/overview-data";

// The single visual expression of the pipeline every part of this
// application is organised around. `counts` comes straight from the
// database (see loadPipelineCounts); a stage that is not implemented yet is
// never given a fabricated number - it says "Coming next" instead.
export function PipelineStrip({ counts }: { counts: PipelineCounts }) {
  return (
    <div className="flex flex-wrap items-stretch gap-1.5">
      {PIPELINE.map((stage, index) => {
        const count = counts[stage.id];
        return (
          <div key={stage.id} className="flex items-stretch gap-1.5">
            <Link
              href={stage.href}
              className={cn(
                "flex min-w-36 flex-col justify-between gap-2 rounded-lg border p-3 transition-colors hover:bg-muted",
                !stage.implemented && "border-dashed",
              )}
            >
              <div className="flex items-center gap-2 text-sm font-medium">
                <stage.icon className="size-4 text-muted-foreground" />
                {stage.label}
              </div>
              {stage.implemented ? (
                <p className="text-2xl font-semibold tabular-nums">
                  {count.error ? "—" : (count.value ?? "—")}
                </p>
              ) : (
                <Badge variant="secondary" className="w-fit">
                  Coming next
                </Badge>
              )}
            </Link>
            {index < PIPELINE.length - 1 && (
              <div className="flex items-center text-muted-foreground">
                <ArrowRight className="size-4" />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
