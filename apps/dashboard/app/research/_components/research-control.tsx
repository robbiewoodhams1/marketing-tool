"use client";

import { useState, useTransition } from "react";
import { type StartResearchState, startResearch } from "../actions";
import { DEPTHS, PLATFORMS } from "../_lib/research-config";
import { ResearchFilters } from "./research-filters";

const field =
  "mt-1 w-full rounded border border-foreground/20 bg-background px-3 py-2 text-sm";

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
      className="mt-6 max-w-3xl space-y-6"
      aria-busy={pending}
    >
      <div>
        <label htmlFor="query" className="text-sm font-medium">
          What do you want to learn?
        </label>
        <input
          id="query"
          name="query"
          required
          disabled={pending}
          placeholder="UK sole trader accounting content"
          aria-invalid={Boolean(errors.query)}
          className={field}
        />
        {errors.query && (
          <p className="mt-1 text-sm text-red-600">{errors.query}</p>
        )}
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <label htmlFor="audience" className="text-sm font-medium">
            Audience
          </label>
          <input
            id="audience"
            name="audience"
            disabled={pending}
            placeholder="UK sole traders"
            aria-invalid={Boolean(errors.audience)}
            className={field}
          />
          {errors.audience && (
            <p className="mt-1 text-sm text-red-600">{errors.audience}</p>
          )}
        </div>
        <div>
          <label htmlFor="objective" className="text-sm font-medium">
            Objective
          </label>
          <textarea
            id="objective"
            name="objective"
            rows={2}
            disabled={pending}
            placeholder="Find content that attracts potential TradeFlow users"
            aria-invalid={Boolean(errors.objective)}
            className={field}
          />
          {errors.objective && (
            <p className="mt-1 text-sm text-red-600">{errors.objective}</p>
          )}
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <fieldset disabled={pending}>
          <legend className="text-sm font-medium">Sources</legend>
          <div className="mt-2 space-y-2">
            {PLATFORMS.map((p) => (
              <label
                key={p.id}
                className={`flex items-center gap-2 text-sm ${p.available ? "" : "text-foreground/50"}`}
              >
                <input
                  type="checkbox"
                  name="platforms"
                  value={p.id}
                  defaultChecked={p.id === "youtube"}
                  disabled={!p.available}
                />
                {p.label}
                {!p.available && <span className="text-xs">Coming soon</span>}
              </label>
            ))}
          </div>
          {errors.platforms && (
            <p className="mt-1 text-sm text-red-600">{errors.platforms}</p>
          )}
        </fieldset>

        <fieldset disabled={pending}>
          <legend className="text-sm font-medium">Research depth</legend>
          <div className="mt-2 flex gap-4">
            {DEPTHS.map((d) => (
              <label key={d.id} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="depth"
                  value={d.id}
                  defaultChecked={d.id === "standard"}
                />
                {d.label}
              </label>
            ))}
          </div>
          <p className="mt-2 text-xs text-foreground/60">
            Prepared, not yet applied: the research engine does not use depth
            yet.
          </p>
        </fieldset>
      </div>

      <ResearchFilters disabled={pending} errors={errors} />

      {state.message && (
        <p role="alert" className="text-sm text-red-600">
          {state.message}
        </p>
      )}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={pending}
          className="rounded bg-foreground px-5 py-2 text-sm font-medium text-background disabled:opacity-50"
        >
          {pending ? "Starting…" : "Start research"}
        </button>
      </div>
    </form>
  );
}
