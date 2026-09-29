// Media Direction: human-editable visual guidance for media generation, kept
// separate from the Production spec itself (see the Python side's
// research.media_direction module docstring for the full architectural
// rationale). This file holds only the pure shape/parsing logic so it can be
// unit tested with `node --test`, without a browser or Supabase - mirrors
// app/research/_lib/synthesis.ts's split of pure logic from the page.

export type MediaDirectionRow = {
  target_subject: string | null;
  appearance: string | null;
  setting: string | null;
  visual_style: string | null;
  camera_composition: string | null;
  lighting: string | null;
  avoid: string | null;
  custom_instructions: string | null;
  additional_direction: string | null;
};

export const EMPTY_MEDIA_DIRECTION: MediaDirectionRow = {
  target_subject: null,
  appearance: null,
  setting: null,
  visual_style: null,
  camera_composition: null,
  lighting: null,
  avoid: null,
  custom_instructions: null,
  additional_direction: null,
};

// Order here is the order fields appear in the form. `kind` picks the input
// control; the freeform fields (avoid/custom/additional) get a multi-line
// Textarea, the rest a single-line Input.
export const MEDIA_DIRECTION_FIELDS: {
  name: keyof MediaDirectionRow;
  label: string;
  kind: "input" | "textarea";
}[] = [
  { name: "target_subject", label: "Target person / subject", kind: "input" },
  { name: "appearance", label: "Appearance", kind: "input" },
  { name: "setting", label: "Setting / environment", kind: "input" },
  { name: "visual_style", label: "Visual style", kind: "input" },
  { name: "camera_composition", label: "Camera / composition", kind: "input" },
  { name: "lighting", label: "Lighting", kind: "input" },
  { name: "avoid", label: "Things to avoid", kind: "textarea" },
  { name: "custom_instructions", label: "Custom instructions", kind: "textarea" },
  { name: "additional_direction", label: "Additional direction", kind: "textarea" },
];

/** Blank/whitespace-only fields become `null`, never an empty string, so an
 * untouched or cleared field reads back as "not set" - matching the Python
 * side's `MediaDirection.is_empty()`/`to_snapshot()`. */
export function parseMediaDirectionForm(formData: FormData): MediaDirectionRow {
  const row = { ...EMPTY_MEDIA_DIRECTION };
  for (const field of MEDIA_DIRECTION_FIELDS) {
    const raw = formData.get(field.name);
    const trimmed = typeof raw === "string" ? raw.trim() : "";
    row[field.name] = trimmed === "" ? null : trimmed;
  }
  return row;
}

export function fieldCount(row: Partial<MediaDirectionRow> | null | undefined): number {
  if (!row) return 0;
  return MEDIA_DIRECTION_FIELDS.filter((f) => Boolean(row[f.name])).length;
}

export function hasAnyDirection(row: Partial<MediaDirectionRow> | null | undefined): boolean {
  return fieldCount(row) > 0;
}
