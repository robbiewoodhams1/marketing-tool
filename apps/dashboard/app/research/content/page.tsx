import { createClient } from "@/supabase/server";

function formatNumber(value: number | null) {
  return value === null ? "—" : value.toLocaleString();
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

export default async function ContentPage() {
  const supabase = await createClient();

  const { data: content, error } = await supabase
    .from("content")
    .select(
      "id, research_job_id, platform, url, title, creator, published_at, views, likes, comments_count, topic, pain_point, hook, hook_type, format, created_at",
    )
    .order("created_at", { ascending: false });

  return (
    <main className="p-8">
      <h1 className="text-xl font-bold">Content</h1>

      {error && (
        <p className="mt-4 text-red-600">
          Error loading content: {error.message}
        </p>
      )}

      {!error && content?.length === 0 && (
        <p className="mt-4">No content yet.</p>
      )}

      {!error && content && content.length > 0 && (
        <table className="mt-4 border-collapse text-sm">
          <thead>
            <tr>
              <th className="border px-2 py-1 text-left">Title</th>
              <th className="border px-2 py-1 text-left">Platform</th>
              <th className="border px-2 py-1 text-left">Creator</th>
              <th className="border px-2 py-1 text-left">Published</th>
              <th className="border px-2 py-1 text-right">Views</th>
              <th className="border px-2 py-1 text-right">Likes</th>
              <th className="border px-2 py-1 text-right">Comments</th>
              <th className="border px-2 py-1 text-left">Topic</th>
              <th className="border px-2 py-1 text-left">Pain point</th>
              <th className="border px-2 py-1 text-left">Hook</th>
              <th className="border px-2 py-1 text-left">Hook type</th>
              <th className="border px-2 py-1 text-left">Format</th>
              <th className="border px-2 py-1 text-left">Job</th>
              <th className="border px-2 py-1 text-left">Created</th>
            </tr>
          </thead>
          <tbody>
            {content.map((item) => (
              <tr key={item.id}>
                <td className="border px-2 py-1">
                  {item.url ? (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline"
                    >
                      {item.title ?? item.url}
                    </a>
                  ) : (
                    (item.title ?? "—")
                  )}
                </td>
                <td className="border px-2 py-1">{item.platform ?? "—"}</td>
                <td className="border px-2 py-1">{item.creator ?? "—"}</td>
                <td className="border px-2 py-1">
                  {formatDate(item.published_at)}
                </td>
                <td className="border px-2 py-1 text-right">
                  {formatNumber(item.views)}
                </td>
                <td className="border px-2 py-1 text-right">
                  {formatNumber(item.likes)}
                </td>
                <td className="border px-2 py-1 text-right">
                  {formatNumber(item.comments_count)}
                </td>
                <td className="border px-2 py-1">{item.topic ?? "—"}</td>
                <td className="border px-2 py-1">{item.pain_point ?? "—"}</td>
                <td className="border px-2 py-1">{item.hook ?? "—"}</td>
                <td className="border px-2 py-1">{item.hook_type ?? "—"}</td>
                <td className="border px-2 py-1">{item.format ?? "—"}</td>
                <td className="border px-2 py-1 font-mono text-xs">
                  {item.research_job_id?.slice(0, 8) ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {formatDate(item.created_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
