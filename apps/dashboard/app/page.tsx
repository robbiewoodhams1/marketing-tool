import { createClient } from "@/supabase/server";

export default async function TestDatabasePage() {
  const supabase = await createClient();

  const { data, error } = await supabase
    .from("research_jobs")
    .select("*")
    .limit(10);

  if (error) {
    return (
      <main className="p-8">
        <h1 className="text-xl font-bold">Database error</h1>
        <pre className="mt-4">{error.message}</pre>
      </main>
    );
  }

  return (
    <main className="p-8">
      <h1 className="text-xl font-bold">Supabase connected</h1>

      <pre className="mt-4">
        {JSON.stringify(data, null, 2)}
      </pre>
    </main>
  );
}