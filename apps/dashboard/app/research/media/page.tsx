import { AlertTriangle, Boxes, Film, ImageOff, Mic } from "lucide-react";
import { createClient } from "@/supabase/server";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Separator } from "@/components/ui/separator";
import { latestAssemblyByProduction, formatDuration, type AssemblyRow } from "./_lib/assembly-data";
import {
  completedSceneCount,
  formatCost,
  formatCostTotals,
  groupAssetsByRun,
  sumCosts,
  voiceInfo,
  type MediaAssetRow,
} from "./_lib/media-data";

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

export default async function MediaPage({
  searchParams,
}: {
  searchParams: Promise<{ production?: string | string[] }>;
}) {
  const supabase = await createClient();
  const params = await searchParams;
  const productionId = Array.isArray(params.production) ? params.production[0] : params.production;

  let runQuery = supabase
    .from("media_runs")
    .select(
      "id, production_id, image_provider, image_model, video_provider, video_model, voice_provider, voice_model, scene_count, created_at",
    )
    .order("created_at", { ascending: false });
  if (productionId) runQuery = runQuery.eq("production_id", productionId);
  const { data: runs, error } = await runQuery;

  const runIds = (runs ?? []).map((r) => r.id);
  const { data: assets } = runIds.length
    ? await supabase
        .from("media_assets")
        .select(
          "id, media_run_id, production_id, scene_number, asset_type, audio_subtype, status, provider, model, prompt, generation_config, duration_seconds, storage_url, error_message, cost_amount, cost_currency, created_at",
        )
        .in("media_run_id", runIds)
    : { data: [] as MediaAssetRow[] };

  const groups = groupAssetsByRun(runs ?? [], assets ?? []);

  let assemblyQuery = supabase
    .from("media_assemblies")
    .select(
      "id, production_id, status, source_scene_count, source_duration_seconds, output_duration_seconds, output_storage_url, output_mime_type, error_message, created_at, completed_at",
    )
    .order("created_at", { ascending: true });
  if (productionId) assemblyQuery = assemblyQuery.eq("production_id", productionId);
  const { data: assemblies } = await assemblyQuery;
  const latestAssembly = latestAssemblyByProduction((assemblies ?? []) as AssemblyRow[]);

  const productionIds = [
    ...new Set([...groups.map((g) => g.productionId), ...(assemblies ?? []).map((a) => a.production_id)]),
  ];
  const { data: productions } = productionIds.length
    ? await supabase.from("productions").select("id, title").in("id", productionIds)
    : { data: [] as { id: string; title: string | null }[] };
  const productionTitle = new Map((productions ?? []).map((p) => [p.id, p.title]));

  const totalAssets = groups.reduce((n, g) => n + g.assets.length, 0);
  const completedAssets = groups.reduce((n, g) => n + g.assets.filter((a) => a.status === "completed").length, 0);
  const grandTotalCosts = (() => {
    const totals = new Map<string, number>();
    for (const g of groups) for (const t of g.totalCosts) totals.set(t.currency, (totals.get(t.currency) ?? 0) + t.amount);
    return [...totals.entries()].map(([currency, amount]) => ({ currency, amount }));
  })();

  return (
    <main className="flex-1 space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Media</h1>
        <p className="mt-1 text-muted-foreground">
          Real generated assets from production scenes, through the Gemini image, Veo video and Gemini
          voice providers.
          {totalAssets > 0 && ` ${completedAssets} of ${totalAssets} scene attempts completed.`}
        </p>
        {grandTotalCosts.length > 0 && (
          <p className="mt-1 text-sm">
            <span className="text-muted-foreground">Total spend shown: </span>
            <span className="font-medium">{formatCostTotals(grandTotalCosts)}</span>
          </p>
        )}
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Error loading media</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}

      {!error &&
        productionIds
          .filter((pid) => latestAssembly.has(pid))
          .map((pid) => {
            const asm = latestAssembly.get(pid)!;
            const generatedCosts = sumCosts(
              groups.filter((g) => g.productionId === pid).flatMap((g) => g.assets),
            );
            return (
              <Card key={`assembly-${pid}`}>
                <CardHeader>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Film className="size-4 text-muted-foreground" />
                      <p className="text-sm font-medium">
                        Assembly · {productionTitle.get(pid) ?? "Production"}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">{asm.source_scene_count} scene(s)</Badge>
                      <Badge variant={asm.status === "completed" ? "secondary" : "destructive"}>
                        {asm.status}
                      </Badge>
                    </div>
                  </div>
                </CardHeader>
                <Separator />
                <CardContent className="space-y-4 pt-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    {asm.status === "completed" && asm.output_storage_url ? (
                      <div className="flex aspect-[9/16] max-h-96 items-center justify-center overflow-hidden rounded-lg bg-muted">
                        <video src={asm.output_storage_url} controls className="size-full object-contain" />
                      </div>
                    ) : (
                      <Empty className="border">
                        <EmptyMedia variant="icon">
                          <Film />
                        </EmptyMedia>
                        <EmptyTitle>Assembly failed</EmptyTitle>
                        {asm.error_message && <EmptyDescription>{asm.error_message}</EmptyDescription>}
                      </Empty>
                    )}
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 self-start text-sm">
                      <div>
                        <dt className="text-xs text-muted-foreground">Source duration</dt>
                        <dd>{formatDuration(asm.source_duration_seconds)}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Final duration</dt>
                        <dd>{formatDuration(asm.output_duration_seconds)}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Created</dt>
                        <dd>{formatDate(asm.created_at)}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-muted-foreground">Completed</dt>
                        <dd>{formatDate(asm.completed_at)}</dd>
                      </div>
                      <div className="col-span-2 mt-1 border-t pt-3">
                        <dt className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                          Cost breakdown
                        </dt>
                        <dd className="mt-1 space-y-0.5">
                          <p>Generated media cost: {formatCostTotals(generatedCosts)}</p>
                          <p>+ Assembly: £0 (no external cost - local ffmpeg processing only)</p>
                          <p className="font-medium text-foreground">= Finished media: {formatCostTotals(generatedCosts)}</p>
                        </dd>
                      </div>
                    </dl>
                  </div>
                </CardContent>
              </Card>
            );
          })}

      {!error && groups.length === 0 && (
        <Empty>
          <EmptyMedia variant="icon">
            <Boxes />
          </EmptyMedia>
          <EmptyTitle>No media generated yet</EmptyTitle>
          <EmptyDescription>
            Run <code className="rounded bg-muted px-1 py-0.5 text-xs">python -m research.media</code> against a
            persisted production to generate scene assets here.
          </EmptyDescription>
        </Empty>
      )}

      {!error &&
        groups.map((group) => (
          <Card key={group.runId}>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-medium">
                    {productionTitle.get(group.productionId) ?? "Production"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {group.provider}/{group.model} · {formatDate(group.createdAt)}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{completedSceneCount(group.assets)}/{group.sceneCount} scene(s)</Badge>
                  <Badge variant="secondary">Run total: {formatCostTotals(group.totalCosts)}</Badge>
                </div>
              </div>
            </CardHeader>
            <Separator />
            <CardContent className="pt-4">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {group.assets.map((asset) => {
                  const isAudio = asset.asset_type === "audio";
                  const { voice, language } = voiceInfo(asset.generation_config);
                  return (
                    <Card key={asset.id} className="overflow-hidden py-0">
                      <div
                        className={
                          isAudio
                            ? "flex min-h-32 flex-col items-center justify-center gap-2 bg-muted p-4"
                            : "flex aspect-[9/16] items-center justify-center bg-muted"
                        }
                      >
                        {asset.status === "completed" && asset.storage_url ? (
                          isAudio ? (
                            <>
                              <Mic className="size-6 text-muted-foreground" />
                              <audio src={asset.storage_url} controls className="w-full" />
                            </>
                          ) : asset.asset_type === "image" ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={asset.storage_url}
                              alt={`Scene ${asset.scene_number}`}
                              className="size-full object-cover"
                            />
                          ) : (
                            <video src={asset.storage_url} controls className="size-full object-cover" />
                          )
                        ) : (
                          <ImageOff className="size-8 text-muted-foreground" />
                        )}
                      </div>
                      <CardContent className="space-y-1 py-3 text-xs text-muted-foreground">
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-medium text-foreground">
                            Scene {asset.scene_number} · {asset.audio_subtype ?? asset.asset_type}
                          </p>
                          <Badge variant={asset.status === "completed" ? "secondary" : "destructive"} className="text-xs">
                            {asset.status}
                          </Badge>
                        </div>
                        {isAudio && asset.prompt && (
                          <p className="line-clamp-3">
                            <span className="font-medium text-foreground">Script: </span>
                            {asset.prompt}
                          </p>
                        )}
                        {isAudio && (voice || language) && (
                          <p>
                            Voice: <span className="text-foreground">{voice ?? "—"}</span>
                            {language && ` (${language})`}
                          </p>
                        )}
                        {asset.duration_seconds != null && <p>Duration: {formatDuration(asset.duration_seconds)}</p>}
                        <p>
                          Cost:{" "}
                          <span className="font-medium text-foreground">
                            {formatCost(asset.cost_amount, asset.cost_currency)}
                          </span>
                        </p>
                        <p>{formatDate(asset.created_at)}</p>
                        {asset.status === "failed" && asset.error_message && (
                          <p className="text-destructive">{asset.error_message}</p>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        ))}
    </main>
  );
}
