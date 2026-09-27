import { formatDate } from "../_lib/format";
import type {
  EvidenceView,
  InsightView,
  ObservationView,
  SourceLink,
  SupportItem,
} from "../_lib/synthesis";

// Raw source evidence and derived analysis look different on purpose: a quote
// from a transcript, title or comment is what was actually said; a quote from
// an interpretation is a model's earlier analysis of it.
const ORIGIN_STYLE: Record<EvidenceView["origin"], string> = {
  raw: "border-foreground/30 bg-foreground/5",
  derived: "border-blue-500/50 bg-blue-500/10",
  unresolved: "border-red-500/40 bg-red-500/5",
};

function VideoLine({ content }: { content: SourceLink | null }) {
  if (!content) {
    return <span className="text-foreground/60">Video not available</span>;
  }
  const label = content.title ?? "Untitled video";
  const meta = [content.creator, content.platform].filter(Boolean).join(" · ");
  return (
    <span>
      {content.url ? (
        <a href={content.url} target="_blank" rel="noopener noreferrer" className="underline">
          {label}
        </a>
      ) : (
        <span className="font-medium">{label}</span>
      )}
      {meta && <span className="text-foreground/60"> — {meta}</span>}
    </span>
  );
}

function EvidenceItem({ evidence }: { evidence: EvidenceView }) {
  return (
    <li className={`rounded border p-3 text-sm ${ORIGIN_STYLE[evidence.origin]}`}>
      <p className="flex flex-wrap items-center gap-x-2 text-xs text-foreground/70">
        <span className="font-semibold uppercase tracking-wide">{evidence.originLabel}</span>
        <span aria-hidden>·</span>
        <span>{evidence.fieldLabel}</span>
      </p>

      {evidence.kind === "content" && (
        <p className="mt-1">
          <VideoLine content={evidence.content} />
        </p>
      )}

      {evidence.kind === "comment" && (
        <p className="mt-1">
          Comment
          {evidence.comment && (evidence.comment.likes !== null || evidence.comment.type) && (
            <span className="text-foreground/60">
              {" ("}
              {[
                evidence.comment.type,
                evidence.comment.likes !== null ? `${evidence.comment.likes} likes` : null,
              ]
                .filter(Boolean)
                .join(", ")}
              {")"}
            </span>
          )}{" "}
          on <VideoLine content={evidence.content} />
        </p>
      )}

      {evidence.kind === "interpretation" && (
        <p className="mt-1">
          Classification of <VideoLine content={evidence.content} />
          {evidence.interpretation?.provenance && (
            <span className="text-foreground/60">
              {" "}
              — by {evidence.interpretation.provenance}
              {evidence.interpretation.createdAt
                ? `, ${formatDate(evidence.interpretation.createdAt)}`
                : ""}
            </span>
          )}
        </p>
      )}

      {evidence.kind === "unresolved" && (
        <p role="alert" className="mt-1 text-red-600">
          {evidence.reason}
        </p>
      )}

      <blockquote className="mt-2 border-l-2 border-foreground/30 pl-3 italic">
        “{evidence.quote}”
      </blockquote>

      {evidence.kind === "comment" && evidence.comment?.text && evidence.comment.text !== evidence.quote && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-foreground/70">Full comment</summary>
          <p className="mt-1 whitespace-pre-wrap">{evidence.comment.text}</p>
        </details>
      )}

      {evidence.kind === "interpretation" && evidence.interpretation === null && (
        <p className="mt-2 text-xs text-foreground/60">The interpretation could not be loaded.</p>
      )}
      {evidence.kind === "interpretation" && evidence.interpretation && evidence.interpretation.fields.length > 0 && (
        <dl className="mt-2 space-y-1 text-xs">
          {evidence.interpretation.fields.map(({ name, label, field }) => (
            <div key={name}>
              <dt className="inline font-medium">{label}: </dt>
              <dd className="inline">
                {field.value ?? "—"}
                {field.confidence !== null && (
                  <span className="text-foreground/60"> (confidence {field.confidence.toFixed(2)})</span>
                )}
                {field.evidence && (
                  <span className="block text-foreground/60">
                    The classifier’s own evidence: “{field.evidence}”
                  </span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

function EvidenceList({ items }: { items: EvidenceView[] }) {
  return (
    <ul className="mt-2 space-y-2">
      {items.map((e) => (
        <EvidenceItem key={e.id} evidence={e} />
      ))}
    </ul>
  );
}

function Observation({ observation }: { observation: ObservationView }) {
  const count = observation.evidence.length;
  return (
    <li className="rounded border border-foreground/15 p-3">
      <p className="text-sm">
        <span className="font-medium">Observation {observation.index + 1}.</span>{" "}
        {observation.text}
      </p>
      {count === 0 ? (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
          No evidence rows are recorded for this observation.
        </p>
      ) : (
        <details className="mt-2" open={count <= 2}>
          <summary className="cursor-pointer text-sm text-foreground/70">
            Evidence ({count})
          </summary>
          <EvidenceList items={observation.evidence} />
        </details>
      )}
    </li>
  );
}

function SupportGrid({ items }: { items: SupportItem[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-foreground/60">No support figures were recorded.</p>;
  }
  return (
    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      {items.map((item) => (
        <div key={item.label} className="flex justify-between gap-4 border-b border-foreground/10 pb-1">
          <dt className="text-foreground/60">{item.label}</dt>
          <dd className="font-medium">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function InsightCard({ insight }: { insight: InsightView }) {
  return (
    <article className="rounded border border-foreground/20 p-5" aria-labelledby={`insight-${insight.id}`}>
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={`insight-${insight.id}`} className="text-base font-semibold">
          {insight.title}
        </h3>
        <p className="flex flex-wrap items-center gap-3 text-sm">
          <span className="rounded border border-foreground/20 px-2 py-0.5 text-xs">
            {insight.pattern.label}
          </span>
          <span title="The synthesis model’s own estimate; not a calibrated probability">
            {insight.confidenceText}
          </span>
        </p>
      </header>

      <p className="mt-3">{insight.statement}</p>

      <dl className="mt-4 space-y-3 text-sm">
        {insight.explanation && (
          <div>
            <dt className="font-medium">Why the observations support this</dt>
            <dd className="text-foreground/80">{insight.explanation}</dd>
          </div>
        )}
        {insight.confidenceRationale && (
          <div>
            <dt className="font-medium">Confidence rationale</dt>
            <dd className="text-foreground/80">{insight.confidenceRationale}</dd>
          </div>
        )}
        {insight.limitations && (
          <div>
            <dt className="font-medium">Limitations</dt>
            <dd className="text-foreground/80">{insight.limitations}</dd>
          </div>
        )}
      </dl>

      <section className="mt-5" aria-label="Support">
        <h4 className="text-sm font-semibold">Support</h4>
        <div className="mt-2">
          {insight.support === null ? (
            <p className="text-sm text-foreground/60">Support data could not be read.</p>
          ) : (
            <SupportGrid items={insight.support} />
          )}
        </div>
        {insight.statisticsCited.length > 0 && (
          <details className="mt-2 text-sm">
            <summary className="cursor-pointer text-foreground/70">
              Dataset statistics cited ({insight.statisticsCited.length})
            </summary>
            <ul className="mt-1 list-disc pl-5 font-mono text-xs">
              {insight.statisticsCited.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <section className="mt-5" aria-label="Observations">
        <h4 className="text-sm font-semibold">Observations and evidence</h4>
        {insight.observations.length === 0 ? (
          <p className="mt-2 text-sm text-foreground/60">No observations could be read.</p>
        ) : (
          <ol className="mt-2 space-y-3">
            {insight.observations.map((o) => (
              <Observation key={o.index} observation={o} />
            ))}
          </ol>
        )}
        {insight.otherSupporting.length > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer text-sm text-foreground/70">
              Other supporting evidence ({insight.otherSupporting.length})
            </summary>
            <EvidenceList items={insight.otherSupporting} />
          </details>
        )}
      </section>

      {insight.counterEvidence.length > 0 && (
        <section className="mt-5" aria-label="Counter-evidence">
          <details open>
            <summary className="cursor-pointer text-sm font-semibold">
              Counter-evidence ({insight.counterEvidence.length})
            </summary>
            <EvidenceList items={insight.counterEvidence} />
          </details>
        </section>
      )}

      {insight.problems.length > 0 && (
        <ul role="status" className="mt-4 list-disc pl-5 text-xs text-amber-700 dark:text-amber-400">
          {insight.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </article>
  );
}
