"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw, Search } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useState } from "react";
import { api, type FilingsResponse, type SearchResponse } from "@/lib/api/endpoints";
import { isReadable, snippetParts, viewerHref } from "@/lib/filings";
import { formatDate, formatInteger } from "@/lib/format";
import { hasRole, useSession } from "@/lib/session";
import { ErrorNotice, Panel, SourceList, StatusPill } from "./ui";

const PAGE_SIZE = 50;
const FORM_FILTERS = [
  { label: "All", forms: "" },
  { label: "10-K", forms: "10-K,10-K/A" },
  { label: "10-Q", forms: "10-Q,10-Q/A" },
  { label: "8-K", forms: "8-K,8-K/A" },
  { label: "Form 4", forms: "4,4/A" },
  { label: "Proxy", forms: "DEF 14A" },
];

function SearchResults({ ticker, data }: { ticker: string; data: SearchResponse }) {
  if (data.hits.length === 0) {
    return (
      <p className="panel-copy">
        No indexed passage matches. Only filings that have been opened or indexed for search are
        searched.
      </p>
    );
  }
  return (
    <ol className="search-hits">
      {data.hits.map((hit) => (
        <li key={`${hit.filing.accession}-${hit.section_key}-${hit.char_start}`}>
          <Link href={viewerHref(ticker, hit.filing.accession, {
            section: hit.section_key, start: hit.char_start, end: hit.char_end,
          })}>
            <span className="hit-cite">
              {hit.filing.form} · filed {formatDate(hit.filing.filed_date)} · {hit.section_title}
            </span>
            <span className="hit-snippet">
              {snippetParts(hit.snippet).map((part, index) =>
                part.mark ? <mark key={index}>{part.text}</mark> : <span key={index}>{part.text}</span>)}
            </span>
          </Link>
          <small className="hit-why">
            {[
              hit.text_rank ? `keyword rank ${hit.text_rank}` : null,
              hit.vector_rank ? `semantic rank ${hit.vector_rank}` : null,
            ].filter(Boolean).join(" · ")}
          </small>
        </li>
      ))}
    </ol>
  );
}

function IndexControls({ ticker, data }: { ticker: string; data: FilingsResponse }) {
  const queryClient = useQueryClient();
  const index = useMutation({
    mutationFn: () => api.indexFilings(ticker, { forms: ["10-K", "10-Q", "8-K"], limit: 8, embed_limit: 200 }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["filings", ticker] }),
  });
  const history = useMutation({
    mutationFn: () => api.loadFilingHistory(ticker),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["filings", ticker] }),
  });
  const remaining = data.passages_embeddable - data.passages_embedded;
  return (
    <div className="index-controls">
      <button className="button-secondary" disabled={index.isPending} onClick={() => index.mutate()} type="button"
        title="Load the newest 10-K, 10-Q and 8-K documents, then embed up to 200 passages">
        <RefreshCw size={14} className={index.isPending ? "spin" : ""} />
        {index.isPending ? "Indexing… (can take a minute)" : remaining > 0 ? "Index for search (continue)" : "Index latest filings"}
      </button>
      {!data.history_loaded && (
        <button className="button-link" disabled={history.isPending} onClick={() => history.mutate()} type="button">
          {history.isPending ? "Loading…" : "Load older filings"}
        </button>
      )}
      {index.data && (
        <small>
          Loaded {Object.entries(index.data.outcome).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ") || "no new documents"}
          {" · "}embedded {formatInteger(index.data.embedded)} passages
          {index.data.embedding_remaining > 0 ? ` (${formatInteger(index.data.embedding_remaining)} left)` : ""}
        </small>
      )}
      {history.data && <small>Added {formatInteger(history.data.added)} older filings.</small>}
      {(index.isError || history.isError) && <ErrorNotice error={index.error ?? history.error} title="Request failed" />}
    </div>
  );
}

export function SecTab({ ticker }: { ticker: string }) {
  const { data: session } = useSession();
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const filings = useQuery({
    queryKey: ["filings", ticker, filter, offset],
    queryFn: () => api.filings(ticker, filter, offset, PAGE_SIZE),
    placeholderData: (previous) => previous,
  });
  const search = useQuery({
    queryKey: ["filing-search", ticker, query],
    queryFn: () => api.searchFilings(query, ticker),
    enabled: query.length >= 2,
  });
  const data = filings.data;
  const canIndex = hasRole(session?.user.role, "analyst");

  function submit(event: FormEvent) {
    event.preventDefault();
    setQuery(draft.trim());
  }

  return (
    <div className="company-tab-body">
      <Panel kicker="FULL TEXT · CITED PASSAGES" title="Search this company's filings">
        <form className="inline-form" onSubmit={submit} role="search">
          <input aria-label="Search filings" placeholder="e.g. supplier concentration, export controls, share repurchases"
            value={draft} onChange={(event) => setDraft(event.target.value)} />
          <button className="button-primary" disabled={draft.trim().length < 2} type="submit"><Search size={14} /> Search</button>
        </form>
        {data && (
          <p className="panel-copy">
            {formatInteger(data.documents_loaded)} documents indexed · {formatInteger(data.passages)} passages ·{" "}
            {data.passages_embeddable > 0
              ? `${formatInteger(data.passages_embedded)} of ${formatInteger(data.passages_embeddable)} embedded for semantic search`
              : "semantic search needs indexed filings"}
            . Financial statements and exhibits are searched by keyword only.
          </p>
        )}
        {canIndex && data && <IndexControls ticker={ticker} data={data} />}
        {search.isFetching && <div className="chart-placeholder">Searching…</div>}
        {search.isError && <ErrorNotice error={search.error} title="Search failed" />}
        {search.data && (
          <>
            <small className="search-mode">
              {search.data.mode === "hybrid"
                ? `Keyword + semantic (${search.data.embedding_model})`
                : "Keyword search only (no embedding model configured)"}
            </small>
            <SearchResults ticker={ticker} data={search.data} />
          </>
        )}
      </Panel>

      <Panel kicker={data ? `SEC EDGAR · ${formatInteger(data.total)} FILINGS` : "SEC EDGAR"} title="Filings"
        className={filings.isFetching && data ? "is-refetching" : ""}>
        <div className="segmented form-filter" role="group" aria-label="Form type">
          {FORM_FILTERS.map((option) => (
            <button aria-pressed={filter === option.forms} key={option.label} type="button"
              onClick={() => { setFilter(option.forms); setOffset(0); }}>{option.label}</button>
          ))}
        </div>
        {filings.isPending && <div className="chart-placeholder">Loading the SEC filing index…</div>}
        {filings.isError && <ErrorNotice error={filings.error} title="Filings unavailable" />}
        {data?.message && <p className="quality-warning">{data.message}</p>}
        {data && data.status !== "current" && (
          <StatusPill tone={data.status === "stale" ? "warn" : "off"}>{data.status.replace("_", " ").toUpperCase()}</StatusPill>
        )}
        {data && (
          <div className="table-wrap">
            <table className="data-table filings-table">
              <thead>
                <tr><th>Filed</th><th>Form</th><th>Period</th><th>Description</th><th /></tr>
              </thead>
              <tbody>
                {data.filings.map((filing) => (
                  <tr key={filing.accession}>
                    <td className="mono">{filing.filed_date}</td>
                    <td><b>{filing.form}</b></td>
                    <td className="mono">{filing.report_date ?? "—"}</td>
                    <td>
                      {filing.items.length > 0 ? filing.items.join(" · ") : filing.description ?? "—"}
                      {filing.document_status === "loaded" && (
                        <small>{filing.form.startsWith("4") ? "Transactions parsed" : "Text indexed"}</small>
                      )}
                      {filing.document_status === "not_issuer" && <small>Filed by this company as an owner of another issuer</small>}
                      {filing.document_status === "failed" && <small>{filing.document_error}</small>}
                    </td>
                    <td className="filing-links">
                      {isReadable(filing) && (
                        <Link href={viewerHref(ticker, filing.accession)}>Read</Link>
                      )}
                      <a href={filing.sec_url} rel="noreferrer noopener" target="_blank" title="Filing index on sec.gov">
                        SEC <ExternalLink size={11} />
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.total > PAGE_SIZE && (
          <div className="pager">
            <button className="button-secondary" disabled={offset === 0} type="button"
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Newer</button>
            <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {formatInteger(data.total)}</span>
            <button className="button-secondary" disabled={offset + PAGE_SIZE >= data.total} type="button"
              onClick={() => setOffset(offset + PAGE_SIZE)}>Older</button>
          </div>
        )}
        {data && (
          <details className="sources">
            <summary>Sources ({data.sources.length})</summary>
            <SourceList sources={data.sources} />
          </details>
        )}
      </Panel>
    </div>
  );
}
