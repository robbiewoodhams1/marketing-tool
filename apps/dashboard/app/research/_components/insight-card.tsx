import { AlertTriangle } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
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
  raw: "border-border bg-muted/40",
  derived: "border-blue-500/40 bg-blue-500/5",
  unresolved: "border-destructive/40 bg-destructive/5",
};

function VideoLine({ content }: { content: SourceLink | null }) {
  if (!content) {
    return <span className="text-muted-foreground">Video not available</span>;
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
      {meta && <span className="text-muted-foreground"> — {meta}</span>}
    </span>
  );
}

function EvidenceItem({ evidence }: { evidence: EvidenceView }) {
  return (
    <li className={cn("rounded-lg border p-3 text-sm", ORIGIN_STYLE[evidence.origin])}>
      <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
        <span className="font-semibold tracking-wide uppercase">{evidence.originLabel}</span>
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
            <span className="text-muted-foreground">
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
            <span className="text-muted-foreground">
              {" "}
              — by {evidence.interpretation.provenance}
              {evidence.interpretation.createdAt ? `, ${formatDate(evidence.interpretation.createdAt)}` : ""}
            </span>
          )}
        </p>
      )}

      {evidence.kind === "unresolved" && (
        <p role="alert" className="mt-1 text-destructive">
          {evidence.reason}
        </p>
      )}

      <blockquote className="mt-2 border-l-2 border-border pl-3 italic">“{evidence.quote}”</blockquote>

      {evidence.kind === "comment" && evidence.comment?.text && evidence.comment.text !== evidence.quote && (
        <Collapsible className="mt-2">
          <CollapsibleTrigger className="text-xs text-muted-foreground underline-offset-2 hover:underline">
            Full comment
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-1 text-xs whitespace-pre-wrap">
            {evidence.comment.text}
          </CollapsibleContent>
        </Collapsible>
      )}

      {evidence.kind === "interpretation" && evidence.interpretation === null && (
        <p className="mt-2 text-xs text-muted-foreground">The interpretation could not be loaded.</p>
      )}
      {evidence.kind === "interpretation" && evidence.interpretation && evidence.interpretation.fields.length > 0 && (
        <dl className="mt-2 space-y-1 text-xs">
          {evidence.interpretation.fields.map(({ name, label, field }) => (
            <div key={name}>
              <dt className="inline font-medium">{label}: </dt>
              <dd className="inline">
                {field.value ?? "—"}
                {field.confidence !== null && (
                  <span className="text-muted-foreground"> (confidence {field.confidence.toFixed(2)})</span>
                )}
                {field.evidence && (
                  <span className="block text-muted-foreground">
                    The classifier&rsquo;s own evidence: &ldquo;{field.evidence}&rdquo;
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
    <li className="rounded-lg border p-3">
      <p className="text-sm">
        <span className="font-medium">Observation {observation.index + 1}.</span> {observation.text}
      </p>
      {count === 0 ? (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
          No evidence rows are recorded for this observation.
        </p>
      ) : (
        <Collapsible defaultOpen={count <= 2} className="mt-2">
          <CollapsibleTrigger className="text-sm text-muted-foreground hover:underline">
            Evidence ({count})
          </CollapsibleTrigger>
          <CollapsibleContent>
            <EvidenceList items={observation.evidence} />
          </CollapsibleContent>
        </Collapsible>
      )}
    </li>
  );
}

function SupportGrid({ items }: { items: SupportItem[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">No support figures were recorded.</p>;
  }
  return (
    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      {items.map((item) => (
        <div key={item.label} className="flex justify-between gap-4 border-b pb-1">
          <dt className="text-muted-foreground">{item.label}</dt>
          <dd className="font-medium">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function InsightCard({ insight }: { insight: InsightView }) {
  return (
    <Card aria-labelledby={`insight-${insight.id}`}>
      <CardHeader>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <CardTitle id={`insight-${insight.id}`} className="text-base">
            {insight.title}
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge variant="outline">{insight.pattern.label}</Badge>
            <span
              className="text-muted-foreground"
              title="The synthesis model’s own estimate; not a calibrated probability"
            >
              {insight.confidenceText}
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <p>{insight.statement}</p>

        <dl className="space-y-3 text-sm">
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

        <Separator />

        <section aria-label="Support">
          <h4 className="text-sm font-semibold">Support</h4>
          <div className="mt-2">
            {insight.support === null ? (
              <p className="text-sm text-muted-foreground">Support data could not be read.</p>
            ) : (
              <SupportGrid items={insight.support} />
            )}
          </div>
          {insight.statisticsCited.length > 0 && (
            <Collapsible className="mt-2 text-sm">
              <CollapsibleTrigger className="text-muted-foreground hover:underline">
                Dataset statistics cited ({insight.statisticsCited.length})
              </CollapsibleTrigger>
              <CollapsibleContent>
                <ul className="mt-1 list-disc pl-5 font-mono text-xs">
                  {insight.statisticsCited.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </CollapsibleContent>
            </Collapsible>
          )}
        </section>

        <Separator />

        <section aria-label="Observations">
          <h4 className="text-sm font-semibold">Observations and evidence</h4>
          {insight.observations.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">No observations could be read.</p>
          ) : (
            <ol className="mt-2 space-y-3">
              {insight.observations.map((o) => (
                <Observation key={o.index} observation={o} />
              ))}
            </ol>
          )}
          {insight.otherSupporting.length > 0 && (
            <Collapsible className="mt-3">
              <CollapsibleTrigger className="text-sm text-muted-foreground hover:underline">
                Other supporting evidence ({insight.otherSupporting.length})
              </CollapsibleTrigger>
              <CollapsibleContent>
                <EvidenceList items={insight.otherSupporting} />
              </CollapsibleContent>
            </Collapsible>
          )}
        </section>

        {insight.counterEvidence.length > 0 && (
          <>
            <Separator />
            <section aria-label="Counter-evidence">
              <Collapsible defaultOpen>
                <CollapsibleTrigger className="text-sm font-semibold hover:underline">
                  Counter-evidence ({insight.counterEvidence.length})
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <EvidenceList items={insight.counterEvidence} />
                </CollapsibleContent>
              </Collapsible>
            </section>
          </>
        )}

        {insight.problems.length > 0 && (
          <Alert>
            <AlertTriangle />
            <AlertDescription>
              <ul className="list-disc pl-5">
                {insight.problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}
