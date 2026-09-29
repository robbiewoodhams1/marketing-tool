import { ChevronDown } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Field,
  FieldError,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  CONTENT_AGES,
  CONTENT_TYPES,
  type FormErrors,
  SEARCH_STRATEGIES,
} from "../_lib/research-config";

export function ResearchFilters({
  disabled,
  errors,
}: {
  disabled: boolean;
  errors: FormErrors;
}) {
  return (
    <Collapsible className="rounded-lg border p-4">
      <CollapsibleTrigger className="flex w-full items-center justify-between text-sm font-medium">
        <span>
          Advanced controls{" "}
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            Prepared, not yet applied
          </span>
        </span>
        <ChevronDown className="size-4 text-muted-foreground" />
      </CollapsibleTrigger>

      <CollapsibleContent className="mt-4">
        <div className="grid gap-6 md:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="contentAge">Content age</FieldLabel>
            <Select name="contentAge" defaultValue="any" disabled={disabled}>
              <SelectTrigger id="contentAge">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CONTENT_AGES.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field data-invalid={Boolean(errors.minViews)}>
            <FieldLabel htmlFor="minViews">Minimum views</FieldLabel>
            <Input
              id="minViews"
              name="minViews"
              type="number"
              min={0}
              step={1}
              inputMode="numeric"
              placeholder="No minimum"
              disabled={disabled}
              aria-invalid={Boolean(errors.minViews)}
            />
            {errors.minViews && <FieldError>{errors.minViews}</FieldError>}
          </Field>

          <FieldSet disabled={disabled}>
            <FieldLegend variant="label">Content type</FieldLegend>
            <FieldGroup className="gap-2">
              {CONTENT_TYPES.map((t) => (
                <Field key={t.id} orientation="horizontal">
                  <Checkbox id={`type-${t.id}`} name="contentTypes" value={t.id} defaultChecked />
                  <FieldLabel htmlFor={`type-${t.id}`} className="font-normal">
                    {t.label}
                  </FieldLabel>
                </Field>
              ))}
            </FieldGroup>
          </FieldSet>

          <FieldSet disabled={disabled}>
            <FieldLegend variant="label">Search strategy</FieldLegend>
            <FieldGroup className="gap-2">
              {SEARCH_STRATEGIES.map((s) => (
                <Field key={s.id} orientation="horizontal">
                  <Checkbox
                    id={`strategy-${s.id}`}
                    name="searchStrategies"
                    value={s.id}
                    defaultChecked={s.defaultOn}
                  />
                  <FieldLabel htmlFor={`strategy-${s.id}`} className="font-normal">
                    {s.label}
                  </FieldLabel>
                </Field>
              ))}
            </FieldGroup>
          </FieldSet>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
