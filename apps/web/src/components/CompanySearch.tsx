"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Search } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api/endpoints";
import { useDebounced } from "@/lib/use-debounced";
import { ErrorNotice, InlineState } from "./ui";

export function CompanySearch({ query, onQueryChange, limit = 8, autoFocus = false }: {
  query: string;
  onQueryChange: (value: string) => void;
  limit?: number;
  autoFocus?: boolean;
}) {
  const term = useDebounced(query.trim());
  const results = useQuery({
    queryKey: ["company-search", term, limit],
    queryFn: ({ signal }) => api.searchCompanies(term, limit, signal),
    enabled: term.length > 0,
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <form className="company-search" role="search" onSubmit={(event) => event.preventDefault()}>
        <Search size={18} />
        <input
          aria-label="Search companies by name or ticker"
          autoFocus={autoFocus}
          maxLength={120}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search by company name or ticker"
          value={query}
        />
      </form>
      {term && (
        <div className="search-results" aria-live="polite">
          {results.isPending && <InlineState>Searching the company directory…</InlineState>}
          {results.isError && <ErrorNotice error={results.error} title="Search unavailable" />}
          {results.data && results.data.items.length === 0 && (
            <InlineState>No listed company matches “{term}” in the directory.</InlineState>
          )}
          {results.data?.items.map((company) => (
            <Link className="company-result" href={`/companies/${encodeURIComponent(company.ticker)}`}
              key={`${company.ticker}-${company.exchange}`}>
              <span className="company-result-symbol">{company.ticker.slice(0, 1)}</span>
              <span className="company-result-copy">
                <strong>{company.name}</strong>
                <small>
                  {company.ticker}
                  {company.exchange ? ` · ${company.exchange}` : ""}
                  {company.industry ? ` · ${company.industry}` : ""}
                </small>
              </span>
              <ArrowUpRight size={16} />
            </Link>
          ))}
          {results.data && results.data.total > results.data.items.length && (
            <p className="search-more">
              Showing {results.data.items.length} of {results.data.total.toLocaleString("en-US")} matches.
            </p>
          )}
        </div>
      )}
    </>
  );
}
