import type { Filing } from "@/lib/api/endpoints";

/** SEC Form 4 transaction codes (General Instructions, Form 4, item 8). */
export const TRANSACTION_CODES: Record<string, string> = {
  P: "Open-market purchase",
  S: "Open-market sale",
  A: "Grant or award",
  M: "Option exercise",
  X: "Exercise of in-the-money derivative",
  C: "Conversion of derivative",
  F: "Tax withholding",
  G: "Gift",
  D: "Disposition to issuer",
  J: "Other",
  K: "Equity swap",
  I: "Discretionary transaction",
  W: "Will or inheritance",
  Z: "Voting trust",
  L: "Small acquisition",
  U: "Tender of shares",
  V: "Voluntary report",
  E: "Expiration of short derivative",
  H: "Expiration of long derivative",
  O: "Exercise of out-of-the-money derivative",
};

export function transactionLabel(code: string | null | undefined): string {
  if (!code) return "—";
  return TRANSACTION_CODES[code] ?? `Code ${code}`;
}

// The API marks matched terms in snippets with Unicode private-use characters.
const MARK_START = "";
const MARK_END = "";

/** Split a search snippet into plain and highlighted runs, in order. */
export function snippetParts(snippet: string): { text: string; mark: boolean }[] {
  const parts: { text: string; mark: boolean }[] = [];
  for (const [index, piece] of snippet.split(MARK_START).entries()) {
    if (index === 0) {
      if (piece) parts.push({ text: piece, mark: false });
      continue;
    }
    const [marked, ...rest] = piece.split(MARK_END);
    if (marked) parts.push({ text: marked, mark: true });
    const tail = rest.join("");
    if (tail) parts.push({ text: tail, mark: false });
  }
  return parts;
}

/** In-app viewer link; optional section and passage offsets for citations. */
export function viewerHref(
  ticker: string,
  accession: string,
  citation?: { section: string; start?: number; end?: number },
): string {
  const base = `/companies/${encodeURIComponent(ticker)}/filings/${accession}`;
  if (!citation) return base;
  const query = new URLSearchParams({ section: citation.section });
  if (citation.start !== undefined && citation.end !== undefined) {
    query.set("start", String(citation.start));
    query.set("end", String(citation.end));
  }
  return `${base}?${query}`;
}

/** Whether the app can show this filing's text (HTML/text primary documents only). */
export function isReadable(filing: Pick<Filing, "form" | "document_url">): boolean {
  if (filing.form === "4" || filing.form === "4/A") return false;
  const url = filing.document_url?.toLowerCase() ?? "";
  return /\.(htm|html|xhtml|txt)$/.test(url);
}

/** Split section text into paragraphs, keeping each one's offset in the section text. */
export function paragraphsWithOffsets(text: string): { start: number; end: number; text: string }[] {
  const result: { start: number; end: number; text: string }[] = [];
  let position = 0;
  for (const paragraph of text.split("\n\n")) {
    if (paragraph) result.push({ start: position, end: position + paragraph.length, text: paragraph });
    position += paragraph.length + 2;
  }
  return result;
}
