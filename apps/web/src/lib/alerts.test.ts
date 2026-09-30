import { describe, expect, it } from "vitest";
import { evidenceText } from "@/components/EventTimeline";
import type { TimelineEvent } from "@/lib/api/endpoints";
import { buildRule, describeRule, type RuleDraft } from "./alerts";

const draft = (patch: Partial<RuleDraft>): RuleDraft => ({
  name: " NVDA buys ", kind: "insider", tickers: "nvda, msft  aapl", watchlistId: "", forms: ["8-K"],
  eventTypes: [], firstOnly: true, codes: ["P"], minValue: "$250,000", minMovePct: "5", email: true,
  ...patch,
});

describe("alert rule form", () => {
  it("sends only the parameters of the chosen rule type", () => {
    const rule = buildRule(draft({}));
    expect(rule).toMatchObject({ name: "NVDA buys", kind: "insider", tickers: ["NVDA", "MSFT", "AAPL"], watchlist_id: null });
    expect(rule.params).toEqual({ first_only: true, codes: ["P"], min_value: 250000 });
    expect(buildRule(draft({ kind: "filing" })).params).toEqual({ first_only: true, forms: ["8-K"] });
    expect(buildRule(draft({ kind: "price", minMovePct: "" })).params?.min_move_pct).toBe(5);
    expect(buildRule(draft({ kind: "news", eventTypes: ["m_and_a"] })).params?.event_types).toEqual(["m_and_a"]);
  });

  it("describes rules in plain words", () => {
    expect(describeRule({ kind: "insider", params: { codes: ["P"], min_value: 250000 } })).toBe("Form 4 purchases ≥ $250,000");
    expect(describeRule({ kind: "news", params: { event_types: ["m_and_a"], first_only: true } })).toBe(
      "m and a news events, first reports only",
    );
    expect(describeRule({ kind: "price", params: {} })).toBe("Daily move of 5% or more");
  });
});

describe("event evidence", () => {
  const base = {
    id: 1, event_type: "earnings", label: "Earnings results", title: "t", occurred_at: "2026-09-30T12:00:00Z",
    novelty: "first", cluster_id: 1, cluster_size: 1, outlets: [], filing: null, news: null,
  } as const;

  it("explains filing and news classifications", () => {
    const filing = { ...base, source_kind: "filing", evidence: { items: ["2.02", "9.01"] } } as unknown as TimelineEvent;
    expect(evidenceText(filing)).toContain("items: 2.02, 9.01");
    const news = {
      ...base, source_kind: "news", cluster_size: 3, evidence: { matched: "acquire", link_method: "title_name" },
    } as unknown as TimelineEvent;
    expect(evidenceText(news)).toBe("Headline matched “acquire”\nLinked by title name\n3 reports of this story");
  });
});
