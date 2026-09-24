import {
  CONTENT_AGES,
  CONTENT_TYPES,
  type FormErrors,
  SEARCH_STRATEGIES,
} from "../_lib/research-config";

const field =
  "mt-1 w-full rounded border border-foreground/20 bg-background px-3 py-2 text-sm";

export function ResearchFilters({
  disabled,
  errors,
}: {
  disabled: boolean;
  errors: FormErrors;
}) {
  return (
    <details className="rounded border border-foreground/15 p-4">
      <summary className="cursor-pointer text-sm font-medium">
        Advanced controls{" "}
        <span className="ml-2 text-xs font-normal text-foreground/60">
          Prepared, not yet applied
        </span>
      </summary>

      <div className="mt-4 grid gap-6 md:grid-cols-2">
        <div>
          <label htmlFor="contentAge" className="text-sm font-medium">
            Content age
          </label>
          <select
            id="contentAge"
            name="contentAge"
            defaultValue="any"
            disabled={disabled}
            className={field}
          >
            {CONTENT_AGES.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="minViews" className="text-sm font-medium">
            Minimum views
          </label>
          <input
            id="minViews"
            name="minViews"
            type="number"
            min={0}
            step={1}
            inputMode="numeric"
            placeholder="No minimum"
            disabled={disabled}
            aria-invalid={Boolean(errors.minViews)}
            className={field}
          />
          {errors.minViews && (
            <p className="mt-1 text-sm text-red-600">{errors.minViews}</p>
          )}
        </div>

        <fieldset disabled={disabled}>
          <legend className="text-sm font-medium">Content type</legend>
          <div className="mt-2 space-y-2">
            {CONTENT_TYPES.map((t) => (
              <label key={t.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  name="contentTypes"
                  value={t.id}
                  defaultChecked
                />
                {t.label}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset disabled={disabled}>
          <legend className="text-sm font-medium">Search strategy</legend>
          <div className="mt-2 space-y-2">
            {SEARCH_STRATEGIES.map((s) => (
              <label key={s.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  name="searchStrategies"
                  value={s.id}
                  defaultChecked={s.defaultOn}
                />
                {s.label}
              </label>
            ))}
          </div>
        </fieldset>
      </div>
    </details>
  );
}
