import Link from "next/link";
import { AlertTriangle, Clapperboard, ImageIcon } from "lucide-react";
import { createClient } from "@/supabase/server";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Separator } from "@/components/ui/separator";
import { fieldCount, type MediaDirectionRow } from "./_lib/media-direction";
import { MediaDirectionForm } from "./_components/media-direction-form";

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

function formatTime(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

type Scene = {
  number: number;
  duration_seconds: number;
  purpose: string;
  voiceover: string;
  on_screen_text: string | null;
  visual_direction: string;
  product_capability_refs: string[];
  transition: string | null;
};

type MediaAssetStatus = { scene_number: number; status: string };

function MediaStatusBadge({ status, productionId }: { status: MediaAssetStatus | undefined; productionId: string }) {
  if (!status) {
    return (
      <Badge variant="outline" className="text-muted-foreground">
        No media yet
      </Badge>
    );
  }
  return (
    <Link href={`/research/media?production=${productionId}`}>
      <Badge variant={status.status === "completed" ? "secondary" : "destructive"}>
        <ImageIcon data-icon="inline-start" />
        {status.status === "completed" ? "Media generated" : "Generation failed"}
      </Badge>
    </Link>
  );
}

export default async function ProductionPage() {
  const supabase = await createClient();

  const { data: productions, error } = await supabase
    .from("productions")
    .select(
      "id, opportunity_id, title, hook, target_audience, objective, platform, estimated_duration_seconds, scenes, script, cta, caption, hashtags, product_capability_refs, limitations, created_at",
    )
    .order("created_at", { ascending: false });

  const opportunityIds = [...new Set((productions ?? []).map((p) => p.opportunity_id))];
  const { data: opportunities } = opportunityIds.length
    ? await supabase.from("opportunities").select("id, title").in("id", opportunityIds)
    : { data: [] as { id: string; title: string | null }[] };
  const opportunityTitle = new Map((opportunities ?? []).map((o) => [o.id, o.title]));

  const productionIds = (productions ?? []).map((p) => p.id);
  const { data: directionRows } = productionIds.length
    ? await supabase
        .from("media_directions")
        .select(
          "production_id, updated_at, target_subject, appearance, setting, visual_style, camera_composition, lighting, avoid, custom_instructions, additional_direction",
        )
        .in("production_id", productionIds)
    : { data: [] as ({ production_id: string; updated_at: string } & MediaDirectionRow)[] };
  const directionByProduction = new Map((directionRows ?? []).map((d) => [d.production_id, d]));

  const { data: mediaAssetRows } = productionIds.length
    ? await supabase
        .from("media_assets")
        .select("production_id, scene_number, status, created_at")
        .in("production_id", productionIds)
        .order("created_at", { ascending: true })
    : { data: [] as { production_id: string; scene_number: number; status: string; created_at: string }[] };

  // Media generation is append-only: a retried scene adds a new row rather
  // than updating the old one, so "the current state of scene N" is its
  // LATEST row.
  const latestStatus = new Map<string, MediaAssetStatus>();
  for (const asset of mediaAssetRows ?? []) {
    latestStatus.set(`${asset.production_id}:${asset.scene_number}`, asset);
  }

  return (
    <main className="max-w-4xl flex-1 space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Production</h1>
        <p className="mt-1 text-muted-foreground">
          Every production here already passed validation - product-truth, scene/duration sanity
          and length checks - before it was persisted.
        </p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Error loading production</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}

      {!error && productions?.length === 0 && (
        <Empty>
          <EmptyMedia variant="icon">
            <Clapperboard />
          </EmptyMedia>
          <EmptyTitle>No productions yet</EmptyTitle>
          <EmptyDescription>
            Productions turn a validated opportunity into a short-form video specification.
          </EmptyDescription>
        </Empty>
      )}

      {!error &&
        productions?.map((p) => {
          const scenes = (Array.isArray(p.scenes) ? p.scenes : []) as Scene[];
          const hashtags = Array.isArray(p.hashtags) ? (p.hashtags as string[]) : [];
          const capabilities = Array.isArray(p.product_capability_refs) ? (p.product_capability_refs as string[]) : [];
          let elapsed = 0;

          return (
            <Card key={p.id}>
              <CardHeader>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <CardTitle className="text-base">{p.title}</CardTitle>
                  <span className="text-xs text-muted-foreground">{formatDate(p.created_at)}</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  From opportunity: {opportunityTitle.get(p.opportunity_id) ?? p.opportunity_id}
                </p>
              </CardHeader>
              <CardContent className="space-y-4">
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
                  <div>
                    <dt className="text-xs text-muted-foreground">Platform</dt>
                    <dd className="capitalize">{p.platform?.replace(/_/g, " ")}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Duration</dt>
                    <dd>{p.estimated_duration_seconds}s</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Audience</dt>
                    <dd className="truncate">{p.target_audience}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Objective</dt>
                    <dd className="capitalize">{p.objective}</dd>
                  </div>
                </dl>

                <div>
                  <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Hook</p>
                  <p className="mt-1 text-sm font-medium">{p.hook}</p>
                </div>

                <Separator />

                <div>
                  <p className="text-sm font-semibold">Scenes</p>
                  <ol className="mt-2 space-y-2">
                    {scenes.map((scene) => {
                      const start = elapsed;
                      elapsed += scene.duration_seconds;
                      return (
                        <li key={scene.number} className="flex gap-3 rounded-lg border p-3 text-sm">
                          <div className="w-20 shrink-0 font-mono text-xs text-muted-foreground">
                            {String(scene.number).padStart(2, "0")}
                            <br />
                            {formatTime(start)}–{formatTime(elapsed)}
                          </div>
                          <div className="min-w-0 flex-1 space-y-1">
                            <p className="font-medium">{scene.purpose}</p>
                            <p className="text-muted-foreground">
                              <span className="font-medium text-foreground">Voiceover: </span>
                              {scene.voiceover}
                            </p>
                            {scene.on_screen_text && (
                              <p className="text-muted-foreground">
                                <span className="font-medium text-foreground">On screen: </span>
                                {scene.on_screen_text}
                              </p>
                            )}
                            <p className="text-muted-foreground">
                              <span className="font-medium text-foreground">Visual: </span>
                              {scene.visual_direction}
                            </p>
                            <div className="flex flex-wrap items-center gap-2 pt-1">
                              {scene.product_capability_refs?.map((c) => (
                                <Badge key={c} variant="outline" className="text-xs">
                                  {c.replace(/_/g, " ")}
                                </Badge>
                              ))}
                              <MediaStatusBadge status={latestStatus.get(`${p.id}:${scene.number}`)} productionId={p.id} />
                            </div>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                </div>

                <Collapsible>
                  <CollapsibleTrigger className="text-sm font-medium hover:underline">
                    Full script
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <pre className="mt-1 rounded-md bg-muted/50 p-3 text-xs whitespace-pre-wrap">{p.script}</pre>
                  </CollapsibleContent>
                </Collapsible>

                <Separator />

                <Collapsible>
                  <CollapsibleTrigger className="flex items-center gap-2 text-sm font-medium hover:underline">
                    Media direction
                    {(() => {
                      const count = fieldCount(directionByProduction.get(p.id));
                      return count > 0 ? (
                        <Badge variant="secondary" className="font-normal">
                          {count} field{count === 1 ? "" : "s"} set
                        </Badge>
                      ) : (
                        <span className="text-xs font-normal text-muted-foreground">Not set</span>
                      );
                    })()}
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <p className="mt-1 text-xs text-muted-foreground">
                      How generated media should visually depict this production - reused across
                      regenerations, never part of the production spec itself.
                    </p>
                    <MediaDirectionForm
                      // Remounts with fresh uncontrolled defaultValues whenever
                      // the saved row actually changes (e.g. right after this
                      // form's own save triggers revalidation) - otherwise Base
                      // UI warns about an uncontrolled field's default value
                      // changing after the input already mounted.
                      key={`${p.id}:${directionByProduction.get(p.id)?.updated_at ?? "unsaved"}`}
                      productionId={p.id}
                      initial={directionByProduction.get(p.id) ?? null}
                    />
                  </CollapsibleContent>
                </Collapsible>

                <Separator />

                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">CTA</p>
                    <p className="mt-1 text-sm">{p.cta}</p>
                  </div>
                  <div>
                    <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Caption</p>
                    <p className="mt-1 text-sm">{p.caption}</p>
                    {hashtags.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {hashtags.map((h) => (
                          <Badge key={h} variant="outline">
                            #{h}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {capabilities.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                      Product capabilities used
                    </p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {capabilities.map((c) => (
                        <Badge key={c} variant="secondary" className="capitalize">
                          {c.replace(/_/g, " ")}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}

                {p.limitations && (
                  <p className="text-xs text-muted-foreground">
                    <span className="font-medium">Limitations: </span>
                    {p.limitations}
                  </p>
                )}
              </CardContent>
            </Card>
          );
        })}
    </main>
  );
}
