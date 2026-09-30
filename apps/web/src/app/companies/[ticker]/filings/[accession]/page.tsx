"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink, GitCompare } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { SectionDiffView } from "@/components/SectionDiffView";
import { ErrorNotice, Panel, SourceList } from "@/components/ui";
import { api } from "@/lib/api/endpoints";
import { paragraphsWithOffsets, viewerHref } from "@/lib/filings";
import { formatDate, formatInteger } from "@/lib/format";

function FilingViewer() {
  const params = useParams<{ ticker: string; accession: string }>();
  const ticker = decodeURIComponent(params.ticker).toUpperCase();
  const accession = decodeURIComponent(params.accession);
  const search = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [compare, setCompare] = useState(false);
  const cited = useRef<HTMLParagraphElement | null>(null);

  const detail = useQuery({
    queryKey: ["filing", ticker, accession],
    queryFn: () => api.filing(ticker, accession),
    staleTime: 10 * 60_000,
  });
  const data = detail.data;
  const sections = data?.sections ?? [];
  const sectionKey = search.get("section") ?? sections[0]?.key;
  const section = sections.find((s) => s.key === sectionKey) ?? sections[0];
  const start = Number(search.get("start") ?? NaN);
  const end = Number(search.get("end") ?? NaN);
  const citing = section?.key === search.get("section") && Number.isFinite(start) && Number.isFinite(end);
  const paragraphs = section ? paragraphsWithOffsets(section.text) : [];
  const isCited = (p: { start: number; end: number }) => citing && p.start < end && p.end > start;
  const firstCited = paragraphs.find(isCited)?.start;

  const diff = useQuery({
    queryKey: ["filing-diff", ticker, accession, section?.key],
    queryFn: () => api.filingDiff(ticker, accession, section!.key),
    enabled: compare && Boolean(section && data?.previous),
    staleTime: 10 * 60_000,
  });

  useEffect(() => {
    cited.current?.scrollIntoView({ block: "center" });
  }, [section?.key, start, data]);

  function choose(key: string) {
    setCompare(false);
    const next = new URLSearchParams({ section: key });
    router.replace(`${pathname}?${next}`, { scroll: false });
  }

  return (
    <>
      <div className="company-breadcrumb">
        <Link href="/companies">COMPANIES</Link> <span>/</span>{" "}
        <Link href={`/companies/${encodeURIComponent(ticker)}?tab=sec`}>{ticker}</Link> <span>/</span> {accession}
      </div>
      {detail.isPending && (
        <div className="page-loading" aria-busy="true">
          Fetching the document from SEC and splitting it into items…
        </div>
      )}
      {detail.isError && <ErrorNotice error={detail.error} title="Filing unavailable" />}
      {data && (
        <>
          <Panel kicker={`${data.filing.form} · FILED ${formatDate(data.filing.filed_date)}`}
            title={data.filing.description && data.filing.description !== data.filing.form
              ? data.filing.description : `${ticker} ${data.filing.form}`}
            action={
              <div className="filing-links">
                {data.filing.document_url && (
                  <a href={data.filing.document_url} rel="noreferrer noopener" target="_blank">Original document <ExternalLink size={12} /></a>
                )}
                <a href={data.filing.sec_url} rel="noreferrer noopener" target="_blank">Filing index <ExternalLink size={12} /></a>
              </div>
            }>
            <dl className="kv-grid">
              <div><dt>Period</dt><dd>{data.filing.report_date ?? "—"}</dd></div>
              <div><dt>Accession</dt><dd>{data.filing.accession}</dd></div>
              <div><dt>Sections</dt><dd>{sections.length}</dd></div>
              {data.previous && (
                <div><dt>Previous {data.previous.form}</dt><dd>
                  <Link href={viewerHref(ticker, data.previous.accession)}>{data.previous.filed_date}</Link>
                </dd></div>
              )}
            </dl>
            {data.filing.document_status !== "loaded" && (
              <p className="quality-warning">{data.filing.document_error ?? "The document text is not available."}</p>
            )}
            {data.filing.items.length > 0 && <p className="panel-copy">Items: {data.filing.items.join(" · ")}</p>}
          </Panel>

          {section && (
            <section className="filing-layout">
              <nav className="filing-nav" aria-label="Sections">
                <select aria-label="Section" className="filing-nav-select" value={section.key}
                  onChange={(event) => choose(event.target.value)}>
                  {sections.map((s) => <option key={s.key} value={s.key}>{s.title}</option>)}
                </select>
                <ul>
                  {sections.map((s) => (
                    <li key={s.key}>
                      <button aria-current={s.key === section.key ? "true" : undefined} onClick={() => choose(s.key)} type="button">
                        <span>{s.title}</span><small>{formatInteger(Math.round(s.char_count / 100) / 10)}k</small>
                      </button>
                    </li>
                  ))}
                </ul>
              </nav>
              <article className="panel filing-text">
                <div className="panel-heading">
                  <div><span className="section-kicker">AS FILED · {formatInteger(section.char_count)} CHARACTERS</span><h2>{section.title}</h2></div>
                  {data.previous && (
                    <button aria-pressed={compare} className="button-secondary" onClick={() => setCompare(!compare)} type="button">
                      <GitCompare size={14} /> {compare ? "Show text" : `Compare with ${data.previous.form} of ${data.previous.filed_date.slice(0, 4)}`}
                    </button>
                  )}
                </div>
                {compare ? (
                  <>
                    {diff.isPending && <div className="chart-placeholder">Loading the previous filing and comparing…</div>}
                    {diff.isError && <ErrorNotice error={diff.error} title="Comparison unavailable" />}
                    {diff.data && <SectionDiffView diff={diff.data} />}
                  </>
                ) : section.text ? (
                  paragraphs.map((p) => (
                    <p className={isCited(p) ? "cited" : undefined} key={p.start}
                      ref={p.start === firstCited ? cited : undefined}>
                      {p.text}
                    </p>
                  ))
                ) : (
                  <p className="panel-copy">This section has no text in the filing (for example “Not applicable” or incorporated by reference).</p>
                )}
              </article>
            </section>
          )}
          <Panel kicker="PROVENANCE" title="Source">
            <SourceList sources={data.sources} />
            <p className="panel-copy">
              Text is the filing&apos;s own wording, split into items by heading (extractor {data.extractor_version}).
              Tables are flattened to “ | ”-separated rows; use the original document for layout.
            </p>
          </Panel>
        </>
      )}
    </>
  );
}

export default function FilingPage() {
  return (
    <Suspense>
      <FilingViewer />
    </Suspense>
  );
}
