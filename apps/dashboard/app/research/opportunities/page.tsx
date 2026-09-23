import { createClient } from "@/supabase/server";

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

export default async function OpportunitiesPage() {
  const supabase = await createClient();

  const { data: opportunities, error } = await supabase
    .from("opportunities")
    .select(
      "id, title, description, target_audience, pain_point, suggested_format, suggested_hook, reason, status, created_at, updated_at",
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
              <th className="border px-2 py-1 text-left">Pain point</th>
              <th className="border px-2 py-1 text-left">Format</th>
              <th className="border px-2 py-1 text-left">Hook</th>
              <th className="border px-2 py-1 text-left">Status</th>
              <th className="border px-2 py-1 text-left">Created</th>
            </tr>
          </thead>
          <tbody>
            {opportunities.map((opportunity) => (
              <tr key={opportunity.id}>
                <td className="border px-2 py-1">
                  {opportunity.title ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {opportunity.target_audience ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {opportunity.pain_point ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {opportunity.suggested_format ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {opportunity.suggested_hook ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {opportunity.status ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {formatDate(opportunity.created_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
