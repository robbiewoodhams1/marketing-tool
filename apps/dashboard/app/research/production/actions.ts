"use server";

import { revalidatePath } from "next/cache";
import { createClient } from "@/supabase/server";
import { parseMediaDirectionForm } from "./_lib/media-direction";

export type SaveMediaDirectionState = {
  message?: string;
  savedAt?: number;
};

// Media Direction is the one genuinely user-editable table in this schema
// (see the migration's own docstring): unlike research_jobs' insert-only
// pattern, this upserts in place - the same row is meant to be edited and
// reused, not appended to.
export async function saveMediaDirection(formData: FormData): Promise<SaveMediaDirectionState> {
  const productionId = String(formData.get("production_id") ?? "");
  if (!productionId) return { message: "Missing production id." };

  const row = parseMediaDirectionForm(formData);
  const supabase = await createClient();
  const { error } = await supabase
    .from("media_directions")
    .upsert(
      { production_id: productionId, ...row, updated_at: new Date().toISOString() },
      { onConflict: "production_id" },
    );

  if (error) return { message: `Could not save media direction: ${error.message}` };

  revalidatePath("/research/production");
  return { savedAt: Date.now() };
}
