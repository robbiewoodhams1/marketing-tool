import { createClient } from "@/supabase/server";

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

export default async function InsightsPage() {
  const supabase = await createClient();

  const { data: insights, error } = await supabase
    .from("insights")
    .select("id, title, description, evidence_summary, confidence, created_at")
    .order("created_at", { ascending: false });

  return (
    <main className="p-8">
      <h1 className="text-xl font-bold">Insights</h1>

      {error && (
        <p className="mt-4 text-red-600">
          Error loading insights: {error.message}
        </p>
      )}

      {!error && insights?.length === 0 && (
        <p className="mt-4">No insights yet.</p>
      )}

      {!error && insights && insights.length > 0 && (
        <table className="mt-4 border-collapse text-sm">
          <thead>
            <tr>
              <th className="border px-2 py-1 text-left">Title</th>
              <th className="border px-2 py-1 text-left">Description</th>
              <th className="border px-2 py-1 text-left">Evidence summary</th>
              <th className="border px-2 py-1 text-right">Confidence</th>
              <th className="border px-2 py-1 text-left">Created</th>
            </tr>
          </thead>
          <tbody>
            {insights.map((insight) => (
              <tr key={insight.id}>
                <td className="border px-2 py-1">{insight.title ?? "—"}</td>
                <td className="border px-2 py-1">
                  {insight.description ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {insight.evidence_summary ?? "—"}
                </td>
                <td className="border px-2 py-1 text-right">
                  {insight.confidence ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {formatDate(insight.created_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
