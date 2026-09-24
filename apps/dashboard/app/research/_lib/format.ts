export function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

export function formatNumber(value: number | null): string {
  return value === null ? "—" : value.toLocaleString();
}
