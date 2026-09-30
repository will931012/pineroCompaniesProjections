import { describe, expect, it } from "vitest";
import { diffRuns } from "@/components/SectionDiffView";
import type { DiffBlock } from "@/lib/api/endpoints";
import { isReadable, paragraphsWithOffsets, snippetParts, transactionLabel, viewerHref } from "./filings";

describe("search snippets", () => {
  it("splits highlight markers into marked runs in order", () => {
    expect(snippetParts("Risks from export controls may grow")).toEqual([
      { text: "Risks from ", mark: false },
      { text: "export", mark: true },
      { text: " ", mark: false },
      { text: "controls", mark: true },
      { text: " may grow", mark: false },
    ]);
  });

  it("leaves unmarked snippets whole", () => {
    expect(snippetParts("No highlights here")).toEqual([{ text: "No highlights here", mark: false }]);
  });
});

describe("citations", () => {
  it("links to the exact passage of a section", () => {
    expect(viewerHref("BRK-B", "0001067983-26-000010", { section: "item_1a", start: 10, end: 90 })).toBe(
      "/companies/BRK-B/filings/0001067983-26-000010?section=item_1a&start=10&end=90",
    );
    expect(viewerHref("AAPL", "0000320193-25-000079")).toBe("/companies/AAPL/filings/0000320193-25-000079");
  });

  it("keeps paragraph offsets aligned with the section text", () => {
    const text = "First paragraph.\n\nSecond one.\n\nThird.";
    for (const p of paragraphsWithOffsets(text)) expect(text.slice(p.start, p.end)).toBe(p.text);
    expect(paragraphsWithOffsets(text).map((p) => p.text)).toEqual(["First paragraph.", "Second one.", "Third."]);
  });
});

describe("filing helpers", () => {
  it("labels Form 4 codes and never invents one", () => {
    expect(transactionLabel("S")).toBe("Open-market sale");
    expect(transactionLabel("Q")).toBe("Code Q");
    expect(transactionLabel(null)).toBe("—");
  });

  it("reads only text documents in the app", () => {
    expect(isReadable({ form: "10-K", document_url: "https://www.sec.gov/x/aapl-20250927.htm" })).toBe(true);
    expect(isReadable({ form: "4", document_url: "https://www.sec.gov/x/xslF345X05/f4.xml" })).toBe(false);
    expect(isReadable({ form: "S-8", document_url: "https://www.sec.gov/x/scan.pdf" })).toBe(false);
  });
});

describe("section diff runs", () => {
  const block = (kind: DiffBlock["kind"], text: string): DiffBlock => ({
    kind, before: kind === "added" ? null : text, after: kind === "removed" ? null : text, words: [],
  });

  it("groups consecutive unchanged paragraphs around each change", () => {
    const runs = diffRuns([
      block("same", "a"), block("same", "b"), block("added", "c"), block("same", "d"), block("removed", "e"),
    ]);
    expect(runs.map((r) => (r.kind === "same" ? `same×${r.blocks.length}` : r.block.kind))).toEqual([
      "same×2", "added", "same×1", "removed",
    ]);
  });
});
