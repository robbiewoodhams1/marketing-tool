"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { createClient } from "@/supabase/server";
import { type FormErrors, parseResearchForm } from "./_lib/research-config";

// Status written when the dashboard creates a job. A research engine that
// picks jobs up would move it to "running", then "completed" or "failed"
// (the values the Python persistence layer already uses).
const INITIAL_STATUS = "queued";

export type StartResearchState = {
  errors?: FormErrors;
  message?: string;
};

export async function startResearch(
  formData: FormData,
): Promise<StartResearchState> {
  const parsed = parseResearchForm(formData);
  if (!parsed.ok) return { errors: parsed.errors };

  // parsed.config (platforms, depth, filters) is validated but deliberately
  // not persisted or executed yet: research_jobs has no columns for it and
  // the engine is not connected. Only the job itself is created.
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("research_jobs")
    .insert({
      ...parsed.job,
      status: INITIAL_STATUS,
      // research_jobs.created_at is NOT NULL with no database default, so it
      // must be supplied. started_at stays NULL: nothing has started.
      created_at: new Date().toISOString(),
    })
    .select("id")
    .single();

  if (error || !data) {
    return {
      message: `Could not create the research job: ${error?.message ?? "no row was returned"}`,
    };
  }

  revalidatePath("/research");
  redirect(`/research/${data.id}`); // throws; must stay outside any try/catch
}
