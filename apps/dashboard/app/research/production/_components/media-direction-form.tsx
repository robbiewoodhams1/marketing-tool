"use client";

import { useState, useTransition } from "react";
import { CheckIcon, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { saveMediaDirection, type SaveMediaDirectionState } from "../actions";
import { MEDIA_DIRECTION_FIELDS, type MediaDirectionRow } from "../_lib/media-direction";

export function MediaDirectionForm({
  productionId,
  initial,
}: {
  productionId: string;
  initial: Partial<MediaDirectionRow> | null;
}) {
  const [state, setState] = useState<SaveMediaDirectionState>({});
  const [pending, startTransition] = useTransition();

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    startTransition(async () => {
      try {
        setState(await saveMediaDirection(formData));
      } catch {
        setState({ message: "Could not reach the server. Please try again." });
      }
    });
  }

  const singleLine = MEDIA_DIRECTION_FIELDS.filter((f) => f.kind === "input");
  const freeform = MEDIA_DIRECTION_FIELDS.filter((f) => f.kind === "textarea");

  return (
    <form onSubmit={onSubmit} className="mt-3 max-w-2xl" aria-busy={pending}>
      <input type="hidden" name="production_id" value={productionId} />
      <FieldGroup className="gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          {singleLine.map((f) => (
            <Field key={f.name}>
              <FieldLabel htmlFor={`${productionId}-${f.name}`}>{f.label}</FieldLabel>
              <Input
                id={`${productionId}-${f.name}`}
                name={f.name}
                disabled={pending}
                defaultValue={initial?.[f.name] ?? ""}
              />
            </Field>
          ))}
        </div>
        {freeform.map((f) => (
          <Field key={f.name}>
            <FieldLabel htmlFor={`${productionId}-${f.name}`}>{f.label}</FieldLabel>
            <Textarea
              id={`${productionId}-${f.name}`}
              name={f.name}
              rows={2}
              disabled={pending}
              defaultValue={initial?.[f.name] ?? ""}
            />
          </Field>
        ))}

        {state.message && <p className="text-sm text-destructive">{state.message}</p>}

        <div className="flex items-center gap-2">
          <Button type="submit" size="sm" disabled={pending}>
            {pending && <Loader2 data-icon="inline-start" className="animate-spin" />}
            {pending ? "Saving…" : "Save direction"}
          </Button>
          {state.savedAt && !pending && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <CheckIcon className="size-3.5" /> Saved
            </span>
          )}
        </div>
      </FieldGroup>
    </form>
  );
}
