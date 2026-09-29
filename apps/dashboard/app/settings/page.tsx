import { Database, Settings as SettingsIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { createClient } from "@/supabase/server";

// Read-only: nothing here is editable yet, because there is nothing in the
// backend to edit. This shows what is genuinely true (connection status,
// which environment variables the research service reads) rather than a
// settings form with no effect.
const RESEARCH_SERVICE_ENV_VARS = [
  { name: "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY", note: "Database connection" },
  { name: "LLM_API_KEY / LLM_MODEL", note: "Default Anthropic credentials/model" },
  { name: "SYNTHESIS_MODEL / SYNTHESIS_EFFORT", note: "Synthesis stage override" },
  { name: "OPPORTUNITY_MODEL / OPPORTUNITY_EFFORT", note: "Opportunity Creation stage override" },
  { name: "PRODUCTION_MODEL / PRODUCTION_EFFORT", note: "Production stage override" },
  { name: "GEMINI_API_KEY / MEDIA_IMAGE_MODEL", note: "Media generation (Gemini image provider)" },
];

export default async function SettingsPage() {
  const supabase = await createClient();
  const { error } = await supabase.from("research_jobs").select("id", { count: "exact", head: true });

  return (
    <main className="max-w-2xl flex-1 space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-muted-foreground">
          System configuration is read-only here for now: everything is set through environment
          variables on the research service.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Database className="size-4" />
            Database connection
          </CardTitle>
          <CardDescription>The dashboard&apos;s own connection, checked live.</CardDescription>
        </CardHeader>
        <CardContent>
          <Badge variant={error ? "destructive" : "secondary"}>{error ? "Error" : "Connected"}</Badge>
          {error && <p className="mt-2 text-sm text-destructive">{error.message}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <SettingsIcon className="size-4" />
            Research service configuration
          </CardTitle>
          <CardDescription>
            Environment variables the Python research service reads (values are never shown here).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {RESEARCH_SERVICE_ENV_VARS.map((v, i) => (
            <div key={v.name}>
              {i > 0 && <Separator className="mb-2" />}
              <p className="font-mono text-xs">{v.name}</p>
              <p className="text-muted-foreground">{v.note}</p>
            </div>
          ))}
        </CardContent>
      </Card>
    </main>
  );
}
