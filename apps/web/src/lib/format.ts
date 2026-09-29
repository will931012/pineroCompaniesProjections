const EMPTY = "—";

export function formatPrice(value: number | null | undefined, currency?: string | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const digits = Math.abs(value) < 1 ? 4 : 2;
  const formatted = value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return currency ? `${formatted} ${currency}` : formatted;
}

export function formatSignedNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  const signed = formatSignedNumber(value, digits);
  return signed === EMPTY ? EMPTY : `${signed}%`;
}

export function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return Math.round(value).toLocaleString("en-US");
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return EMPTY;
  // Plain dates (YYYY-MM-DD) are calendar dates; format them without a timezone shift.
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00Z`) : new Date(value);
  if (Number.isNaN(date.getTime())) return EMPTY;
  return date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return EMPTY;
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

/** SEC fiscal year end is MMDD, e.g. "0926" → "Sep 26". */
export function formatFiscalYearEnd(value: string | null | undefined): string {
  if (!value || !/^\d{4}$/.test(value)) return EMPTY;
  const month = Number(value.slice(0, 2));
  const day = Number(value.slice(2));
  if (month < 1 || month > 12 || day < 1 || day > 31) return EMPTY;
  const name = new Date(Date.UTC(2000, month - 1, 1)).toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  return `${name} ${day}`;
}

export function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** Add whole days to a YYYY-MM-DD calendar date (UTC, no local timezone drift). */
export function shiftIsoDate(iso: string, days: number): string {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return isoDate(date);
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return EMPTY;
  return value.toLowerCase().replace(/\b([a-z])/g, (match) => match.toUpperCase());
}
