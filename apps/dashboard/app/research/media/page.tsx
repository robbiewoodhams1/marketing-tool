import { AlertTriangle, Boxes, ImageOff } from "lucide-react";
import { createClient } from "@/supabase/server";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from "@/components/ui/empty";

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

  let query = supabase
    .from("media_assets")
    .select(
      "id, media_run_id, production_id, scene_number, asset_type, status, provider, model, storage_url, error_message, created_at",
    )
    .order("created_at", { ascending: false });
  if (productionId) query = query.eq("production_id", productionId);
  const { data: assets, error } = await query;

  const productionIds = [...new Set((assets ?? []).map((a) => a.production_id))];
  const { data: productions } = productionIds.length
    ? await supabase.from("productions").select("id, title").in("id", productionIds)
    : { data: [] as { id: string; title: string | null }[] };
  const productionTitle = new Map((productions ?? []).map((p) => [p.id, p.title]));

  const total = assets?.length ?? 0;
  const completed = assets?.filter((a) => a.status === "completed").length ?? 0;

  return (
    <main className="flex-1 space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Media</h1>
        <p className="mt-1 text-muted-foreground">
          Real generated assets from production scenes, through the Gemini image provider.
          {total > 0 && ` ${completed} of ${total} scene attempts completed.`}
        </p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Error loading media</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}

      {!error && (!assets || assets.length === 0) && (
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

      {!error && assets && assets.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {assets.map((asset) => (
            <Card key={asset.id} className="overflow-hidden py-0">
              <div className="flex aspect-[9/16] items-center justify-center bg-muted">
                {asset.status === "completed" && asset.storage_url ? (
                  asset.asset_type === "image" ? (
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
              <CardHeader className="pt-4">
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-sm font-medium">
                    {productionTitle.get(asset.production_id) ?? "Production"}
                  </p>
                  <Badge variant={asset.status === "completed" ? "secondary" : "destructive"}>
                    {asset.status}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-1 pb-4 text-xs text-muted-foreground">
                <p>
                  Scene {asset.scene_number} · {asset.asset_type}
                </p>
                <p>
                  {asset.provider}/{asset.model}
                </p>
                <p>{formatDate(asset.created_at)}</p>
                {asset.status === "failed" && asset.error_message && (
                  <p className="text-destructive">{asset.error_message}</p>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </main>
  );
}
