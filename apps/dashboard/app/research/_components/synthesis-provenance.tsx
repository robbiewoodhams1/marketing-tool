import Link from "next/link";
import { formatDate } from "../_lib/format";
import {
  coverageLines,
  insightsHref,
  type SynthesisRun,
} from "../_lib/synthesis";

// Where the synthesis came from: enough to audit it, nothing internal.
export function SynthesisProvenance({
  run,
  isLatest,
  runs,
}: {
  run: SynthesisRun;
  isLatest: boolean;
  runs: SynthesisRun[];
}) {
  const coverage = coverageLines(run.coverage);
  const facts = [
    { label: "Model", value: run.model },
    { label: "Prompt version", value: run.promptVersion },
    { label: "Schema version", value: run.schemaVersion },
    { label: "Run", value: formatDate(run.createdAt) },
    ...coverage,
  ];
  const others = runs.filter((r) => r.id !== run.id);

  return (
    <section aria-label="Synthesis provenance" className="mt-4 rounded border border-foreground/15 p-4">
      <h2 className="text-sm font-semibold">
        Synthesis {isLatest ? "(latest run)" : "(earlier run)"}
      </h2>
      <dl className="mt-2 grid gap-x-8 gap-y-1 text-sm sm:grid-cols-2">
        {facts.map((f) => (
          <div key={f.label} className="flex justify-between gap-4">
            <dt className="text-foreground/60">{f.label}</dt>
            <dd>{f.value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-xs text-foreground/60">
        Insights are derived by a model from the collected evidence and its earlier
        classifications. Each one lists the evidence it rests on.
      </p>

      {others.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-foreground/70">
            {others.length} other synthesis run{others.length === 1 ? "" : "s"} for this job
          </summary>
          <ul className="mt-2 space-y-1">
            {others.map((r) => (
              <li key={r.id}>
                <Link href={insightsHref(r.researchJobId, r.id)} className="underline">
                  {formatDate(r.createdAt)}
                </Link>
                <span className="text-foreground/60">
                  {" "}
                  — {r.model} · {r.promptVersion}
                  {r.insightCount !== null ? ` · ${r.insightCount} insight${r.insightCount === 1 ? "" : "s"}` : ""}
                </span>
              </li>
            ))}
          </ul>
          {!isLatest && (
            <p className="mt-2">
              <Link href={insightsHref(run.researchJobId)} className="underline">
                Show the latest run
              </Link>
            </p>
          )}
        </details>
      )}
    </section>
  );
}
