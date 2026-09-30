"use client";

import { ArrowUpRight, Building2, Database, LineChart } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { CompanySearch } from "@/components/CompanySearch";
import { SystemStatusPanel, useSystemStatus } from "@/components/SystemStatusPanel";
import { PageHeading, Panel } from "@/components/ui";
import { WatchlistPanel } from "@/components/WatchlistPanel";
import { formatDateTime, formatInteger } from "@/lib/format";
import { CURRENT_PHASE, MODULES, isLive } from "@/lib/modules";
import { useSession } from "@/lib/session";

const PLANNED_WIDGETS = [
  { title: "Market overview & indexes", phase: 6 },
  { title: "Macro indicators", phase: 6 },
  { title: "Major news", phase: 4 },
  { title: "Upcoming earnings", phase: 4 },
  { title: "Model signals", phase: 6 },
  { title: "Portfolio & risk alerts", phase: 9 },
];

export default function DashboardPage() {
  const { data: session } = useSession();
  const status = useSystemStatus();
  const [query, setQuery] = useState("");
  const directory = status.data?.directory;
  const market = status.data?.providers.find((p) => p.kind === "market_data");
  const today = new Date().toLocaleDateString("en-US", {
    weekday: "long", year: "numeric", month: "long", day: "numeric",
  }).toUpperCase();

  return (
    <>
      <PageHeading
        kicker={today}
        title={session ? `Welcome, ${session.user.display_name}` : "Research workspace"}
        subtitle="A clear view of the companies and evidence that matter."
      />

      <section className="metric-grid" aria-label="Workspace status">
        <article className="metric-tile">
          <div className="metric-top"><span>COMPANY DIRECTORY</span><Building2 size={16} /></div>
          <div className="metric-value">{directory ? formatInteger(directory.companies) : "—"}</div>
          <div className="metric-foot">
            {directory?.last_synced_at ? `SEC ticker file · synced ${formatDateTime(directory.last_synced_at)}`
              : "Not synced yet · an admin runs the SEC directory sync"}
          </div>
        </article>
        <article className="metric-tile">
          <div className="metric-top"><span>ACTIVE LISTINGS</span><Database size={16} /></div>
          <div className="metric-value">{directory ? formatInteger(directory.active_securities) : "—"}</div>
          <div className="metric-foot">Delisted tickers are retained as inactive history</div>
        </article>
        <article className="metric-tile metric-tile-accent">
          <div className="metric-top"><span>MARKET DATA</span><LineChart size={16} /></div>
          <div className="metric-value metric-state">
            {market ? (market.configured ? market.name ?? "Configured" : "Not configured") : "—"}
          </div>
          <div className="metric-foot">{market?.configured ? "End-of-day bars with provenance" : "No prices are displayed"}</div>
        </article>
      </section>

      <section className="content-grid">
        <Panel kicker="COMPANY INTELLIGENCE" title="Find a company"
          action={<Link className="text-action" href="/companies">Directory <ArrowUpRight size={15} /></Link>}>
          <CompanySearch query={query} onQueryChange={setQuery} limit={6} />
          {!query.trim() && (
            <div className="empty-coverage">
              <div className="empty-icon"><Building2 size={20} /></div>
              <div>
                <strong>Search the SEC-sourced directory</strong>
                <p>Every listing comes from SEC EDGAR&apos;s official ticker file. Company pages show where each value came from.</p>
              </div>
            </div>
          )}
        </Panel>
        <SystemStatusPanel />
      </section>

      <section className="lower-grid">
        <WatchlistPanel />
        <aside className="phase-card">
          <div className="phase-card-top"><span>BUILD STATUS</span><span className="phase-number">{String(CURRENT_PHASE).padStart(2, "0")} / 10</span></div>
          <h2>SEC filings live.</h2>
          <p>Filings split into items, period-over-period changes, insider trades, and cited search over filing text. News and events arrive next.</p>
          <div className="phase-track"><span style={{ width: `${CURRENT_PHASE * 10}%` }} /></div>
          <ul className="planned-widgets">
            {PLANNED_WIDGETS.map((widget) => (
              <li key={widget.title}><span>{widget.title}</span><b>P{widget.phase}</b></li>
            ))}
          </ul>
          <div className="phase-card-bottom">
            <span>{MODULES.filter((m) => isLive(m.phase)).length} live modules · {MODULES.filter((m) => !isLive(m.phase)).length} planned</span>
            <span className="phase-dot" />
          </div>
        </aside>
      </section>
    </>
  );
}
