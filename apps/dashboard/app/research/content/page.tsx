import { createClient } from "@/supabase/server";
import {
  classificationView,
  describeField,
  describeProvenance,
  type ClassificationView,
} from "../_lib/classification";
import {
  JobScopeBanner,
  JobScopeProblem,
  resolveJobScope,
} from "../_lib/job-scope";

function formatNumber(value: number | null) {
  return value === null ? "—" : value.toLocaleString();
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

const cell = "border px-2 py-1";

// The derived cells for one row. Nothing here reads raw content columns.
function ClassificationCells({ view }: { view: ClassificationView }) {
  if (view.kind !== "ok") {
    return (
      <>
        <td className={`${cell} text-foreground/60`} colSpan={5}>
          {view.kind === "none"
            ? "Not classified"
            : "Classification unreadable (malformed result)"}
        </td>
        <td className={`${cell} text-xs`}>
          <Source view={view} />
        </td>
      </>
    );
  }
  const { fields } = view;
  return (
    <>
      {(["topic", "pain_point", "hook", "hook_type", "format"] as const).map(
        (name) => (
          <td key={name} className={cell} title={describeField(fields[name])}>
            {fields[name].value ?? "—"}
          </td>
        ),
      )}
      <td className={`${cell} text-xs`}>
        <Source view={view} />
      </td>
    </>
  );
}

// Which interpretation is being shown, and that others exist.
function Source({ view }: { view: ClassificationView }) {
  if (view.kind === "none") return <>—</>;
  const { provenance, earlier } = view;
  return (
    <>
      <span>{describeProvenance(provenance)}</span>
      <br />
      <span className="text-foreground/60">
        {formatDate(provenance.createdAt)}
        {earlier > 0 ? ` · +${earlier} earlier` : ""}
      </span>
    </>
  );
}

export default async function ContentPage({
  searchParams,
}: {
  searchParams: Promise<{ job?: string | string[] }>;
}) {
  const supabase = await createClient();
  const scope = await resolveJobScope(supabase, (await searchParams).job);

  if (scope.state === "not-found" || scope.state === "error") {
    return (
      <main className="p-8">
        <h1 className="text-xl font-bold">Content</h1>
        <JobScopeProblem scope={scope} />
      </main>
    );
  }

  let query = supabase
    .from("content")
    .select(
      // Raw evidence, plus each row's classification interpretations embedded
      // in the same request (no N+1). Classification is derived analysis and
      // comes only from `interpretations`, never from `content` columns.
      "id, research_job_id, platform, url, title, creator, published_at, views, likes, comments_count, created_at, interpretations(id, analysis_type, model, prompt_version, schema_version, result, created_at)",
    )
    .eq("interpretations.analysis_type", "classification")
    .order("created_at", { ascending: false })
    .order("created_at", { ascending: false, referencedTable: "interpretations" });
  if (scope.state === "ok") query = query.eq("research_job_id", scope.id);
  const { data: content, error } = await query;

  return (
    <main className="p-8">
      <h1 className="text-xl font-bold">Content</h1>
      {scope.state === "ok" && <JobScopeBanner scope={scope} noun="Content" />}

      {error && (
        <p className="mt-4 text-red-600">
          Error loading content: {error.message}
        </p>
      )}

      {!error && content?.length === 0 && (
        <p className="mt-4">
          {scope.state === "ok"
            ? "No content has been collected for this research job yet."
            : "No content yet."}
        </p>
      )}

      {!error && content && content.length > 0 && (
        <table className="mt-4 border-collapse text-sm">
          <thead>
            <tr>
              <th className="border px-2 py-1 text-left" colSpan={9}>
                Raw content
              </th>
              <th
                className="border bg-foreground/5 px-2 py-1 text-left"
                colSpan={6}
              >
                Classification (derived — latest interpretation)
              </th>
            </tr>
            <tr>
              <th className="border px-2 py-1 text-left">Title</th>
              <th className="border px-2 py-1 text-left">Platform</th>
              <th className="border px-2 py-1 text-left">Creator</th>
              <th className="border px-2 py-1 text-left">Published</th>
              <th className="border px-2 py-1 text-right">Views</th>
              <th className="border px-2 py-1 text-right">Likes</th>
              <th className="border px-2 py-1 text-right">Comments</th>
              <th className="border px-2 py-1 text-left">Job</th>
              <th className="border px-2 py-1 text-left">Created</th>
              <th className="border bg-foreground/5 px-2 py-1 text-left">Topic</th>
              <th className="border bg-foreground/5 px-2 py-1 text-left">Pain point</th>
              <th className="border bg-foreground/5 px-2 py-1 text-left">Hook</th>
              <th className="border bg-foreground/5 px-2 py-1 text-left">Hook type</th>
              <th className="border bg-foreground/5 px-2 py-1 text-left">Format</th>
              <th className="border bg-foreground/5 px-2 py-1 text-left">Model · prompt</th>
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
                <td className="border px-2 py-1 font-mono text-xs">
                  {item.research_job_id?.slice(0, 8) ?? "—"}
                </td>
                <td className="border px-2 py-1">
                  {formatDate(item.created_at)}
                </td>
                <ClassificationCells
                  view={classificationView(item.interpretations)}
                />
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
