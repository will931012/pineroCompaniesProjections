"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Building2, CircleAlert } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { AddToWatchlist } from "@/components/AddToWatchlist";
import { KeyMetricsPanel, metricTitle, useCompanyMetrics } from "@/components/CompanySnapshot";
import { FinancialsTab } from "@/components/FinancialsTab";
import { MarketHistory, useDailyBars } from "@/components/MarketHistory";
import { PeersTab } from "@/components/PeersTab";
import { Panel, SourceList, StatusPill } from "@/components/ui";
import { isApiError } from "@/lib/api/client";
import { api, type CompanyProfile } from "@/lib/api/endpoints";
import { formatDate, formatFiscalYearEnd, formatPercent, formatPrice, formatSignedNumber, formatValue, titleCase } from "@/lib/format";
import { COMPANY_TABS, isLive } from "@/lib/modules";
import { usePreferences } from "@/stores/preferences";

const AVAILABILITY_LABELS: Record<string, string> = {
  market_data: "Market data",
  fundamentals: "Fundamentals",
  sec_filings: "SEC filings",
  news: "News & events",
  valuation: "Valuation",
  quant_models: "Quant models",
};

const PROFILE_STATUS: Record<CompanyProfile["profile_status"], { tone: "ok" | "warn" | "off"; label: string }> = {
  current: { tone: "ok", label: "SEC PROFILE CURRENT" },
  stale: { tone: "warn", label: "SEC PROFILE STALE" },
  unavailable: { tone: "warn", label: "SEC PROFILE UNAVAILABLE" },
  not_configured: { tone: "off", label: "SEC NOT CONFIGURED" },
  not_applicable: { tone: "off", label: "NO SEC CIK" },
};

const tabSlug = (label: string) => label.toLowerCase().replaceAll(" ", "-");

export default function CompanyPage() {
  return (
    <Suspense>
      <CompanyView />
    </Suspense>
  );
}

function CompanyView() {
  const params = useParams<{ ticker: string }>();
  const ticker = decodeURIComponent(params.ticker).toUpperCase();
  const profile = useQuery({ queryKey: ["company", ticker], queryFn: () => api.company(ticker) });
  const search = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const liveTabs = COMPANY_TABS.filter((tab) => isLive(tab.phase)).map((tab) => tabSlug(tab.label));
  const tab = liveTabs.find((slug) => slug === search.get("tab")) ?? "overview";

  function selectTab(slug: string) {
    const next = new URLSearchParams(search);
    if (slug === "overview") next.delete("tab");
    else next.set("tab", slug);
    router.replace(next.size ? `${pathname}?${next}` : pathname, { scroll: false });
  }

  if (profile.isError) {
    const notFound = isApiError(profile.error, "company_not_found");
    return (
      <section className="profile-unavailable">
        <span className="profile-unavailable-icon"><CircleAlert size={20} /></span>
        <div>
          <span className="section-kicker">{notFound ? "NOT IN DIRECTORY" : "PROFILE NOT AVAILABLE"}</span>
          <h1>{ticker}</h1>
          <p>{notFound
            ? "No SEC-registered company uses this ticker in the directory. Nothing has been filled in."
            : profile.error.message}</p>
          <Link className="return-link" href="/companies"><ArrowLeft size={15} /> Back to directory</Link>
        </div>
      </section>
    );
  }

  const company = profile.data;
  return (
    <>
      <div className="company-breadcrumb">
        <Link href="/companies">COMPANIES</Link> <span>/</span> {ticker}
      </div>
      <CompanyHeader ticker={ticker} company={company} />
      <nav className="company-tabs" aria-label="Company sections">
        {COMPANY_TABS.map(({ label, phase }) => isLive(phase) ? (
          <button aria-current={tab === tabSlug(label) ? "page" : undefined} key={label} type="button"
            className={`company-tab${tab === tabSlug(label) ? " active" : ""}`} onClick={() => selectTab(tabSlug(label))}>
            {label}
          </button>
        ) : (
          <span className="company-tab disabled" key={label} title={`Planned for Phase ${phase}`}>
            {label}<sup>P{phase}</sup>
          </span>
        ))}
      </nav>
      {!company ? (
        <div className="page-loading" aria-busy="true">Loading company profile…</div>
      ) : tab === "financials" ? (
        <section className="company-tab-body"><FinancialsTab ticker={ticker} /></section>
      ) : tab === "peers" ? (
        <section className="company-tab-body"><PeersTab ticker={ticker} /></section>
      ) : (
        <section className="company-detail-grid">
          <div className="company-main">
            <MarketHistory ticker={ticker} />
            {company.description && (
              <Panel kicker="BUSINESS" title="Description"><p className="prose">{company.description}</p></Panel>
            )}
            <Panel kicker="COVERAGE" title="Data availability">
              <ul className="availability">
                {Object.entries(company.availability).map(([key, value]) => (
                  <li key={key}>
                    <span>{AVAILABILITY_LABELS[key] ?? titleCase(key.replaceAll("_", " "))}</span>
                    <StatusPill tone={value.status === "available" ? "ok" : value.status === "planned" ? "neutral" : "off"}>
                      {value.status === "planned" ? `PLANNED · ${value.detail.toUpperCase()}` : value.status.replace("_", " ").toUpperCase()}
                    </StatusPill>
                  </li>
                ))}
              </ul>
            </Panel>
          </div>
          <div className="company-side">
            <OverviewMetrics ticker={ticker} />
            <IdentityPanel company={company} />
            <Panel kicker="PROVENANCE" title="Sources">
              <SourceList sources={company.sources} />
            </Panel>
          </div>
        </section>
      )}
    </>
  );
}

function CompanyHeader({ ticker, company }: { ticker: string; company?: CompanyProfile }) {
  const range = usePreferences((state) => state.chartRange);
  const bars = useDailyBars(ticker, range);
  const summary = bars.data?.summary;
  const metrics = useCompanyMetrics(ticker, bars);
  const byKey = new Map((metrics.data?.metrics ?? []).map((m) => [m.key, m]));
  const marketCap = byKey.get("market_cap");
  const enterpriseValue = byKey.get("enterprise_value");
  const status = company ? PROFILE_STATUS[company.profile_status] : null;
  const direction = (summary?.change ?? 0) > 0 ? "up" : (summary?.change ?? 0) < 0 ? "down" : "flat";

  return (
    <section className="company-hero">
      <div className="company-monogram"><Building2 size={24} /></div>
      <div className="company-identity">
        <div className="company-ticker">
          {ticker}
          {company?.exchange && <span className="ticker-exchange">{company.exchange}</span>}
          {status && <StatusPill tone={status.tone}>{status.label}</StatusPill>}
        </div>
        <h1>{company?.name ?? "Loading company profile…"}</h1>
        <p>
          {[company?.classification?.sector, company?.classification?.industry, company?.headquarters]
            .filter(Boolean).join(" · ") || "Classification not yet available"}
        </p>
        {company?.profile_message && <p className="profile-message">{company.profile_message}</p>}
      </div>
      <div className="company-price">
        <span>LAST CLOSE{summary ? ` · ${formatDate(summary.as_of)}` : ""}</span>
        <strong>{formatPrice(summary?.last_close)}</strong>
        <small className={`price-change price-${direction}`}>
          {summary?.change !== null && summary?.change !== undefined
            ? `${formatSignedNumber(summary.change)} (${formatPercent(summary.change_percent)})`
            : bars.isError ? "Market data unavailable" : bars.isPending ? "Loading…" : "—"}
        </small>
        <small className="price-meta">
          <span title={marketCap ? metricTitle(marketCap) : "Needs a stored close and SEC cover-page shares outstanding"}>
            Market cap {formatValue(marketCap?.value, "currency")}
          </span>
          {" · "}
          <span title={enterpriseValue ? metricTitle(enterpriseValue) : "Needs market cap, debt, and cash"}>
            EV {formatValue(enterpriseValue?.value, "currency")}
          </span>
        </small>
        {company && <AddToWatchlist ticker={ticker} />}
      </div>
    </section>
  );
}

function OverviewMetrics({ ticker }: { ticker: string }) {
  const range = usePreferences((state) => state.chartRange);
  const bars = useDailyBars(ticker, range);
  const metrics = useCompanyMetrics(ticker, bars);
  if (!metrics.data) return null;
  return <KeyMetricsPanel metrics={metrics.data.metrics} computedAt={metrics.data.computed_at} />;
}

function IdentityPanel({ company }: { company: CompanyProfile }) {
  const rows: [string, string | null][] = [
    ["Legal name", company.name],
    ["CIK", company.cik ? String(company.cik).padStart(10, "0") : null],
    ["Entity type", company.entity_type],
    ["Filer category", company.filer_category],
    ["SIC", company.classification ? `${company.classification.code} · ${company.classification.industry ?? ""}` : null],
    ["SIC division", company.classification?.sector ?? null],
    ["Incorporated", company.state_of_incorporation],
    ["Headquarters", company.headquarters],
    ["Country", company.country],
    ["Fiscal year end", company.fiscal_year_end ? formatFiscalYearEnd(company.fiscal_year_end) : null],
  ];
  return (
    <Panel kicker="COMPANY RECORD" title="Identity" className="identity-panel">
      <dl className="identity-list">
        {rows.map(([label, value]) => (
          <div key={label}><dt>{label}</dt><dd>{value || "—"}</dd></div>
        ))}
        {company.website && (
          <div><dt>Website</dt><dd><a href={company.website} rel="noreferrer noopener" target="_blank">{company.website}</a></dd></div>
        )}
      </dl>
      {company.listings.length > 1 && (
        <>
          <h3 className="subheading">Listings</h3>
          <ul className="listing-list">
            {company.listings.map((listing) => (
              <li key={`${listing.ticker}-${listing.exchange}`}>
                <Link href={`/companies/${encodeURIComponent(listing.ticker)}`}>{listing.ticker}</Link>
                <span>{listing.exchange ?? "—"}</span>
                {!listing.is_active && <em>inactive</em>}
              </li>
            ))}
          </ul>
        </>
      )}
      {company.former_names.length > 0 && (
        <>
          <h3 className="subheading">Former names</h3>
          <ul className="listing-list">
            {company.former_names.map((former) => (
              <li key={`${former.name}-${former.date_from}`}>
                <span>{former.name}</span>
                <span>{former.date_from?.slice(0, 4) ?? "?"}–{former.date_to?.slice(0, 4) ?? "?"}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}
