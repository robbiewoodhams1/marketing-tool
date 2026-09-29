// Small formatting helpers shared across the app shell / Overview. Deliberately
// separate from app/research/_lib/format.ts (which the existing research
// pages already depend on and which this overhaul does not touch) - kept
// tiny and duplicated rather than reaching into a route-private `_lib`.

export function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["week", 60 * 60 * 24 * 7],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
];

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function formatRelativeTime(value: string): string {
  const seconds = (Date.parse(value) - Date.now()) / 1000;
  if (!Number.isFinite(seconds)) return "—";
  if (Math.abs(seconds) < 60) return "just now";
  for (const [unit, secondsInUnit] of UNITS) {
    if (Math.abs(seconds) >= secondsInUnit) {
      return rtf.format(Math.round(seconds / secondsInUnit), unit);
    }
  }
  return rtf.format(Math.round(seconds), "second");
}
