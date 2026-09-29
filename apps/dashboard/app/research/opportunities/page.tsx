import { AlertTriangle, Sparkles } from "lucide-react";
import { createClient } from "@/supabase/server";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Separator } from "@/components/ui/separator";

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{title}</h4>
      <div className="mt-1 text-sm">{children}</div>
    </div>
  );
}

export default async function OpportunitiesPage() {
  const supabase = await createClient();

  const { data: opportunities, error } = await supabase
    .from("opportunities")
    .select(
      "id, title, audience, problem, opportunity, angle, marketing_objective, suggested_formats, cta_direction, confidence, limitations, created_at, opportunity_evidence(insight_id, note, insights(title, statement))",
    )
    .order("created_at", { ascending: false });

  return (
    <main className="max-w-4xl flex-1 space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Opportunities</h1>
        <p className="mt-1 text-muted-foreground">
          Where research evidence and product fit intersect. An opportunity is a creative/business
          problem to solve - not finished content.
        </p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Error loading opportunities</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}

      {!error && opportunities?.length === 0 && (
        <Empty>
          <EmptyMedia variant="icon">
            <Sparkles />
          </EmptyMedia>
          <EmptyTitle>No opportunities yet</EmptyTitle>
          <EmptyDescription>
            Opportunities are created from a research synthesis once it has insights that
            meaningfully connect to what the product does.
          </EmptyDescription>
        </Empty>
      )}

      {!error &&
        opportunities?.map((o) => {
          const formats = Array.isArray(o.suggested_formats) ? (o.suggested_formats as string[]) : [];
          const evidence = Array.isArray(o.opportunity_evidence) ? o.opportunity_evidence : [];
          return (
            <Card key={o.id}>
              <CardHeader>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <CardTitle className="text-base">{o.title}</CardTitle>
                  <span className="text-xs text-muted-foreground">{formatDate(o.created_at)}</span>
                </div>
                <p className="text-sm text-muted-foreground">For: {o.audience}</p>
              </CardHeader>
              <CardContent className="space-y-4">
                <Section title="Research evidence">
                  <p className="text-muted-foreground italic">Why we believe the audience cares.</p>
                  <div className="mt-2 space-y-2">
                    {evidence.length === 0 ? (
                      <p className="text-muted-foreground">No evidence recorded.</p>
                    ) : (
                      evidence.map((e, i) => {
                        const insight = Array.isArray(e.insights) ? e.insights[0] : e.insights;
                        return (
                          <div key={i} className="rounded-md border bg-muted/30 p-3">
                            {insight && <p className="font-medium">{insight.title}</p>}
                            <p className="mt-1 text-muted-foreground">{e.note}</p>
                          </div>
                        );
                      })
                    )}
                  </div>
                </Section>

                <Section title="Problem">
                  <p>{o.problem}</p>
                </Section>

                <Separator />

                <Section title="Opportunity">
                  <p className="font-medium">{o.opportunity}</p>
                  <p className="mt-1 text-muted-foreground">{o.angle}</p>
                </Section>

                <div className="grid gap-4 sm:grid-cols-2">
                  <Section title="Marketing objective">
                    <Badge variant="secondary" className="capitalize">
                      {o.marketing_objective}
                    </Badge>
                  </Section>
                  <Section title="Suggested formats">
                    <div className="flex flex-wrap gap-1">
                      {formats.map((f) => (
                        <Badge key={f} variant="outline" className="capitalize">
                          {f.replace(/_/g, " ")}
                        </Badge>
                      ))}
                    </div>
                  </Section>
                </div>

                <Section title="CTA direction">
                  <p>{o.cta_direction}</p>
                </Section>

                <Separator />

                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Section title="Confidence">
                    <span className="font-medium tabular-nums">{o.confidence?.toFixed(2) ?? "—"}</span>
                  </Section>
                </div>
                {o.limitations && (
                  <p className="text-xs text-muted-foreground">
                    <span className="font-medium">Limitations: </span>
                    {o.limitations}
                  </p>
                )}
              </CardContent>
            </Card>
          );
        })}
    </main>
  );
}
