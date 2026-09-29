"use client";

import { useState, useTransition } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { type StartResearchState, startResearch } from "../actions";
import { DEPTHS, PLATFORMS } from "../_lib/research-config";
import { ResearchFilters } from "./research-filters";

export function ResearchControl() {
  const [state, setState] = useState<StartResearchState>({});
  const [pending, startTransition] = useTransition();
  const errors = state.errors ?? {};

  // Submitted through a handler rather than <form action> so React does not
  // reset the fields when validation fails or creation errors.
  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    startTransition(async () => {
      try {
        // On success the action redirects, so nothing after this runs.
        setState(await startResearch(formData));
      } catch {
        setState({ message: "Could not reach the server. Please try again." });
      }
    });
  }

  return (
    <form
      onSubmit={onSubmit}
      onChange={(event) => {
        const name = (event.target as Element).getAttribute("name") ?? "";
        if (name in errors)
          setState((s) => ({ ...s, errors: { ...s.errors, [name]: undefined } }));
      }}
      noValidate
      className="mt-6 max-w-3xl"
      aria-busy={pending}
    >
      <FieldGroup>
        <Field data-invalid={Boolean(errors.query)}>
          <FieldLabel htmlFor="query">What do you want to learn?</FieldLabel>
          <Input
            id="query"
            name="query"
            required
            disabled={pending}
            placeholder="UK sole trader accounting content"
            aria-invalid={Boolean(errors.query)}
          />
          {errors.query && <FieldError>{errors.query}</FieldError>}
        </Field>

        <div className="grid gap-6 md:grid-cols-2">
          <Field data-invalid={Boolean(errors.audience)}>
            <FieldLabel htmlFor="audience">Audience</FieldLabel>
            <Input
              id="audience"
              name="audience"
              disabled={pending}
              placeholder="UK sole traders"
              aria-invalid={Boolean(errors.audience)}
            />
            {errors.audience && <FieldError>{errors.audience}</FieldError>}
          </Field>
          <Field data-invalid={Boolean(errors.objective)}>
            <FieldLabel htmlFor="objective">Objective</FieldLabel>
            <Textarea
              id="objective"
              name="objective"
              rows={2}
              disabled={pending}
              placeholder="Find content that attracts potential TradeFlow users"
              aria-invalid={Boolean(errors.objective)}
            />
            {errors.objective && <FieldError>{errors.objective}</FieldError>}
          </Field>
        </div>

        <div className="grid gap-6 md:grid-cols-2">
          <FieldSet disabled={pending}>
            <FieldLegend variant="label">Sources</FieldLegend>
            <FieldGroup className="gap-2">
              {PLATFORMS.map((p) => (
                <Field key={p.id} orientation="horizontal">
                  <Checkbox
                    id={`platform-${p.id}`}
                    name="platforms"
                    value={p.id}
                    defaultChecked={p.id === "youtube"}
                    disabled={!p.available}
                  />
                  <FieldLabel htmlFor={`platform-${p.id}`} className="font-normal">
                    {p.label}
                    {!p.available && (
                      <span className="text-xs text-muted-foreground"> Coming soon</span>
                    )}
                  </FieldLabel>
                </Field>
              ))}
            </FieldGroup>
            {errors.platforms && <FieldError>{errors.platforms}</FieldError>}
          </FieldSet>

          <FieldSet disabled={pending}>
            <FieldLegend variant="label">Research depth</FieldLegend>
            <RadioGroup name="depth" defaultValue="standard" className="flex flex-row gap-4">
              {DEPTHS.map((d) => (
                <Field key={d.id} orientation="horizontal">
                  <RadioGroupItem id={`depth-${d.id}`} value={d.id} />
                  <FieldLabel htmlFor={`depth-${d.id}`} className="font-normal">
                    {d.label}
                  </FieldLabel>
                </Field>
              ))}
            </RadioGroup>
            <FieldDescription>
              Prepared, not yet applied: the research engine does not use depth yet.
            </FieldDescription>
          </FieldSet>
        </div>

        <ResearchFilters disabled={pending} errors={errors} />

        {state.message && <FieldError>{state.message}</FieldError>}

        <div className="flex justify-end">
          <Button type="submit" disabled={pending}>
            {pending && <Loader2 data-icon="inline-start" className="animate-spin" />}
            {pending ? "Starting…" : "Start research"}
          </Button>
        </div>
      </FieldGroup>
    </form>
  );
}
