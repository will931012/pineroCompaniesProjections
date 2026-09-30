import type { ScreenMetric } from "@/lib/api/endpoints";

/** Percent metrics are entered as percentages and sent as fractions (15 → 0.15). */
export function toBound(text: string, unit: ScreenMetric["unit"]): number | null {
  if (!text.trim()) return null;
  const value = Number(text);
  if (!Number.isFinite(value)) return null;
  return unit === "percent" ? value / 100 : value;
}
