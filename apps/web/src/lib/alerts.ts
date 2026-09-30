import type { AlertRule, AlertRuleIn } from "@/lib/api/endpoints";

export const KINDS: { value: AlertRuleIn["kind"]; label: string }[] = [
  { value: "news", label: "News event" },
  { value: "earnings", label: "Earnings release (8-K 2.02)" },
  { value: "filing", label: "New SEC filing" },
  { value: "insider", label: "Insider trade (Form 4)" },
  { value: "price", label: "Daily price move" },
];

export type RuleDraft = {
  name: string;
  kind: AlertRuleIn["kind"];
  tickers: string;
  watchlistId: string;
  forms: string[];
  eventTypes: string[];
  firstOnly: boolean;
  codes: string[];
  minValue: string;
  minMovePct: string;
  email: boolean;
};

const number = (text: string): number | undefined => {
  const value = Number(text.replace(/[,$\s]/g, ""));
  return text.trim() && Number.isFinite(value) ? value : undefined;
};

/** The API request for a form draft: only the parameters that apply to the rule type. */
export function buildRule(draft: RuleDraft): AlertRuleIn {
  // first_only matters only for news rules; the API ignores it for the others.
  const params: NonNullable<AlertRuleIn["params"]> = { first_only: draft.firstOnly };
  if (draft.kind === "filing") params.forms = draft.forms;
  if (draft.kind === "news" && draft.eventTypes.length) params.event_types = draft.eventTypes;
  if (draft.kind === "insider") {
    params.codes = draft.codes;
    const minimum = number(draft.minValue);
    if (minimum !== undefined) params.min_value = minimum;
  }
  if (draft.kind === "price") params.min_move_pct = number(draft.minMovePct) ?? 5;
  return {
    name: draft.name.trim(),
    kind: draft.kind,
    params,
    tickers: draft.tickers.split(/[\s,]+/).map((t) => t.trim().toUpperCase()).filter(Boolean),
    watchlist_id: draft.watchlistId || null,
    email: draft.email,
    active: true,
  };
}

/** One-line summary of what a rule watches for. */
export function describeRule(rule: Pick<AlertRule, "kind" | "params">): string {
  const p = rule.params as Record<string, unknown>;
  const list = (value: unknown) => (Array.isArray(value) ? value.join(", ") : "");
  switch (rule.kind) {
    case "filing":
      return `New ${list(p.forms) || "10-K, 10-Q, 8-K"} filings`;
    case "earnings":
      return "Earnings releases (8-K Item 2.02)";
    case "news":
      return `${list(p.event_types)?.replaceAll("_", " ") || "All"} news events${p.first_only === false ? "" : ", first reports only"}`;
    case "insider": {
      const codes = list(p.codes) || "P";
      const minimum = typeof p.min_value === "number" ? ` ≥ $${p.min_value.toLocaleString("en-US")}` : "";
      return `Form 4 ${codes === "P" ? "purchases" : codes === "S" ? "sales" : `codes ${codes}`}${minimum}`;
    }
    case "price":
      return `Daily move of ${p.min_move_pct ?? 5}% or more`;
    default:
      return rule.kind;
  }
}
