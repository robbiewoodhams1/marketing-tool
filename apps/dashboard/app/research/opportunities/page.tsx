import { createClient } from "@/supabase/server";

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

export default async function OpportunitiesPage() {
  const supabase = await createClient();

  const { data: opportunities, error } = await supabase
    .from("opportunities")
    .select(
      "id, title, audience, problem, opportunity, angle, marketing_objective, suggested_formats, cta_direction, confidence, limitations, created_at",
    )
    .order("created_at", { ascending: false });

  return (
    <main className="p-8">
      <h1 className="text-xl font-bold">Opportunities</h1>

      {error && (
        <p className="mt-4 text-red-600">
          Error loading opportunities: {error.message}
        </p>
      )}

      {!error && opportunities?.length === 0 && (
        <p className="mt-4">No opportunities yet.</p>
      )}

      {!error && opportunities && opportunities.length > 0 && (
        <table className="mt-4 border-collapse text-sm">
          <thead>
            <tr>
              <th className="border px-2 py-1 text-left">Title</th>
              <th className="border px-2 py-1 text-left">Audience</th>
              <th className="border px-2 py-1 text-left">Problem</th>
              <th className="border px-2 py-1 text-left">Opportunity</th>
              <th className="border px-2 py-1 text-left">Angle</th>
              <th className="border px-2 py-1 text-left">Objective</th>
              <th className="border px-2 py-1 text-left">Formats</th>
              <th className="border px-2 py-1 text-left">CTA direction</th>
              <th className="border px-2 py-1 text-right">Confidence</th>
              <th className="border px-2 py-1 text-left">Created</th>
            </tr>
          </thead>
          <tbody>
            {opportunities.map((row) => (
              <tr key={row.id}>
                <td className="border px-2 py-1">{row.title ?? "—"}</td>
                <td className="border px-2 py-1">{row.audience ?? "—"}</td>
                <td className="border px-2 py-1">{row.problem ?? "—"}</td>
                <td className="border px-2 py-1">{row.opportunity ?? "—"}</td>
                <td className="border px-2 py-1">{row.angle ?? "—"}</td>
                <td className="border px-2 py-1">{row.marketing_objective ?? "—"}</td>
                <td className="border px-2 py-1">
                  {Array.isArray(row.suggested_formats) ? row.suggested_formats.join(", ") : "—"}
                </td>
                <td className="border px-2 py-1">{row.cta_direction ?? "—"}</td>
                <td className="border px-2 py-1 text-right">
                  {typeof row.confidence === "number" ? row.confidence.toFixed(2) : "—"}
                </td>
                <td className="border px-2 py-1">{formatDate(row.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
