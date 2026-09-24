// The research request as the UI models it.
//
// Only `query`, `audience` and `objective` exist in `research_jobs` and are
// persisted. Everything else here (platforms, depth, filters) is *prepared
// configuration*: it is collected and validated so the shape is settled, but
// nothing stores it or passes it to the research engine yet. Connecting it is
// a later step; until then it must not be presented as having any effect.

export const PLATFORMS = [
  { id: "youtube", label: "YouTube", available: true },
  { id: "tiktok", label: "TikTok", available: false },
  { id: "instagram", label: "Instagram", available: false },
  { id: "google", label: "Google", available: false },
] as const;
export type PlatformId = (typeof PLATFORMS)[number]["id"];

export const DEPTHS = [
  { id: "quick", label: "Quick" },
  { id: "standard", label: "Standard" },
  { id: "deep", label: "Deep" },
] as const;
export type ResearchDepth = (typeof DEPTHS)[number]["id"];

export const CONTENT_AGES = [
  { id: "any", label: "Any time" },
  { id: "30d", label: "Last 30 days" },
  { id: "3m", label: "Last 3 months" },
  { id: "6m", label: "Last 6 months" },
  { id: "12m", label: "Last 12 months" },
] as const;
export type ContentAge = (typeof CONTENT_AGES)[number]["id"];

export const CONTENT_TYPES = [
  { id: "shorts", label: "Shorts" },
  { id: "long_form", label: "Long-form" },
] as const;
export type ContentType = (typeof CONTENT_TYPES)[number]["id"];

export const SEARCH_STRATEGIES = [
  { id: "topic", label: "Search by topic", defaultOn: true },
  { id: "related_terms", label: "Search related terms", defaultOn: true },
  { id: "competitor_channels", label: "Competitor channels", defaultOn: false },
  { id: "trending", label: "Trending content", defaultOn: false },
] as const;
export type SearchStrategy = (typeof SEARCH_STRATEGIES)[number]["id"];

export interface ResearchConfig {
  platforms: PlatformId[];
  depth: ResearchDepth;
  filters: {
    contentAge: ContentAge;
    minViews: number | null;
    contentTypes: ContentType[];
    searchStrategies: SearchStrategy[];
  };
}

/** The fields that map onto `research_jobs` columns. */
export interface ResearchJobInput {
  query: string;
  audience: string | null;
  objective: string | null;
}

export type FormErrors = Partial<
  Record<"query" | "audience" | "objective" | "platforms" | "minViews", string>
>;

export type ParsedResearchForm =
  | { ok: true; job: ResearchJobInput; config: ResearchConfig }
  | { ok: false; errors: FormErrors };

export const MAX_QUERY_LENGTH = 200;
export const MAX_TEXT_LENGTH = 1000;

function pick<T extends string>(
  values: FormDataEntryValue[],
  options: readonly { id: T }[],
): T[] {
  const allowed = new Set<string>(options.map((o) => o.id));
  return values.filter((v): v is T => typeof v === "string" && allowed.has(v));
}

function text(formData: FormData, name: string): string {
  const value = formData.get(name);
  return typeof value === "string" ? value.trim() : "";
}

export function parseResearchForm(formData: FormData): ParsedResearchForm {
  const errors: FormErrors = {};

  const query = text(formData, "query");
  const audience = text(formData, "audience");
  const objective = text(formData, "objective");
  if (!query) errors.query = "Enter what you want to research.";
  else if (query.length > MAX_QUERY_LENGTH)
    errors.query = `Keep this under ${MAX_QUERY_LENGTH} characters.`;
  if (audience.length > MAX_TEXT_LENGTH)
    errors.audience = `Keep this under ${MAX_TEXT_LENGTH} characters.`;
  if (objective.length > MAX_TEXT_LENGTH)
    errors.objective = `Keep this under ${MAX_TEXT_LENGTH} characters.`;

  const availableOnly = PLATFORMS.filter((p) => p.available);
  const platforms = pick(formData.getAll("platforms"), availableOnly);
  if (platforms.length === 0) errors.platforms = "Select at least one platform.";

  const depthValue = text(formData, "depth");
  const depth = DEPTHS.find((d) => d.id === depthValue)?.id ?? "standard";

  const contentAge =
    CONTENT_AGES.find((a) => a.id === text(formData, "contentAge"))?.id ?? "any";

  let minViews: number | null = null;
  const minViewsRaw = text(formData, "minViews");
  if (minViewsRaw) {
    const parsed = Number(minViewsRaw);
    if (!Number.isSafeInteger(parsed) || parsed < 0)
      errors.minViews = "Enter a whole number, 0 or more.";
    else minViews = parsed;
  }

  if (Object.keys(errors).length > 0) return { ok: false, errors };

  return {
    ok: true,
    job: { query, audience: audience || null, objective: objective || null },
    config: {
      platforms,
      depth,
      filters: {
        contentAge,
        minViews,
        contentTypes: pick(formData.getAll("contentTypes"), CONTENT_TYPES),
        searchStrategies: pick(
          formData.getAll("searchStrategies"),
          SEARCH_STRATEGIES,
        ),
      },
    },
  };
}
