// Run with `npm test` (Node's built-in runner; no dependencies).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  EMPTY_MEDIA_DIRECTION,
  fieldCount,
  hasAnyDirection,
  MEDIA_DIRECTION_FIELDS,
  parseMediaDirectionForm,
  type MediaDirectionRow,
} from "./media-direction.ts";

function formDataFrom(fields: Partial<Record<keyof MediaDirectionRow, string>>): FormData {
  const fd = new FormData();
  for (const [key, value] of Object.entries(fields)) fd.set(key, value);
  return fd;
}

test("an empty form parses to the empty direction (every field null)", () => {
  const result = parseMediaDirectionForm(new FormData());
  assert.deepEqual(result, EMPTY_MEDIA_DIRECTION);
});

test("blank and whitespace-only fields become null, not an empty string", () => {
  const result = parseMediaDirectionForm(formDataFrom({ target_subject: "", appearance: "   " }));
  assert.equal(result.target_subject, null);
  assert.equal(result.appearance, null);
});

test("filled-in fields are trimmed and kept", () => {
  const result = parseMediaDirectionForm(formDataFrom({ setting: "  A garage workshop  " }));
  assert.equal(result.setting, "A garage workshop");
});

test("every declared field round-trips through a form and back", () => {
  const row: MediaDirectionRow = {
    target_subject: "A mechanic",
    appearance: "Overalls",
    setting: "A garage",
    visual_style: "Bright and clean",
    camera_composition: "Close up",
    lighting: "Warm",
    avoid: "No competitor logos",
    custom_instructions: "Keep it upbeat",
    additional_direction: "Nothing else",
  };
  const fd = new FormData();
  for (const field of MEDIA_DIRECTION_FIELDS) fd.set(field.name, row[field.name] ?? "");
  assert.deepEqual(parseMediaDirectionForm(fd), row);
});

test("fieldCount counts only the fields that are actually set", () => {
  assert.equal(fieldCount(null), 0);
  assert.equal(fieldCount(EMPTY_MEDIA_DIRECTION), 0);
  assert.equal(fieldCount({ target_subject: "A mechanic", lighting: "Warm" }), 2);
});

test("hasAnyDirection is false for null/empty and true once any field is set", () => {
  assert.equal(hasAnyDirection(null), false);
  assert.equal(hasAnyDirection(EMPTY_MEDIA_DIRECTION), false);
  assert.equal(hasAnyDirection({ avoid: "Do not show a competitor" }), true);
});

test("every field declared for the form has a non-empty label", () => {
  for (const field of MEDIA_DIRECTION_FIELDS) {
    assert.ok(field.label.trim().length > 0, field.name);
  }
});
