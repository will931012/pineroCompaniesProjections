"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { RefreshCw } from "lucide-react";
import { ErrorNotice, InlineState, PageHeading, Panel, StatusPill } from "@/components/ui";
import { api, type Role } from "@/lib/api/endpoints";
import { formatDateTime } from "@/lib/format";
import { hasRole, useSession } from "@/lib/session";

const ROLES: Role[] = ["viewer", "analyst", "admin"];

export default function AdminPage() {
  const { data: session } = useSession();
  if (session && !hasRole(session.user.role, "admin")) {
    return (
      <section className="profile-unavailable">
        <div><span className="section-kicker">FORBIDDEN</span><h1>Administrators only</h1>
          <p>Your role ({session.user.role}) cannot open the admin console.</p></div>
      </section>
    );
  }
  return (
    <>
      <PageHeading kicker="OPERATIONS" title="Administration"
        subtitle="Users and roles, data ingestion, provider health, and the audit trail." />
      <div className="admin-grid">
        <Ingestion />
        <FundamentalsIngestion />
        <Users currentUserId={session?.user.id} />
        <ProviderLog />
        <AuditLog />
      </div>
    </>
  );
}

function FundamentalsIngestion() {
  const queryClient = useQueryClient();
  const [tickers, setTickers] = useState("");
  const sync = useMutation({
    mutationFn: api.syncFundamentals,
    onSettled: () => queryClient.invalidateQueries(),
  });
  const list = tickers.split(/[\s,]+/).map((t) => t.trim().toUpperCase()).filter(Boolean);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (list.length) sync.mutate(list);
  }

  return (
    <Panel kicker="INGESTION" title="SEC financial data">
      <form className="inline-form" onSubmit={submit}>
        <input aria-label="Tickers" placeholder="AAPL, MSFT, NVDA" value={tickers}
          onChange={(event) => setTickers(event.target.value)} />
        <button className="button-primary" disabled={sync.isPending || list.length === 0 || list.length > 25} type="submit">
          <RefreshCw size={14} className={sync.isPending ? "spin" : ""} />
          {sync.isPending ? "Loading…" : "Load"}
        </button>
      </form>
      <p className="panel-copy">
        Fetches XBRL <code>companyfacts</code> for up to 25 tickers, ignoring the refresh interval, and recomputes
        their metrics so they appear in peers and the screener. For the whole directory use
        <code>python -m app.cli sync-fundamentals --all-active</code>.
      </p>
      {sync.isError && <ErrorNotice error={sync.error} title="Load failed" />}
      {sync.data && (
        <ul className="availability">
          {sync.data.map((row) => (
            <li key={row.ticker} title={row.message ?? undefined}>
              <span>{row.ticker}</span>
              <StatusPill tone={row.status === "current" ? "ok" : row.status === "not_available" ? "off" : "warn"}>
                {row.status.replaceAll("_", " ").toUpperCase()}
              </StatusPill>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Ingestion() {
  const queryClient = useQueryClient();
  const sync = useMutation({
    mutationFn: api.syncSecDirectory,
    onSettled: () => queryClient.invalidateQueries(),
  });
  return (
    <Panel kicker="INGESTION" title="SEC company directory"
      action={
        <button className="button-primary" disabled={sync.isPending} onClick={() => sync.mutate()} type="button">
          <RefreshCw size={14} className={sync.isPending ? "spin" : ""} />
          {sync.isPending ? "Syncing…" : "Sync now"}
        </button>
      }>
      <p className="panel-copy">
        Loads SEC&apos;s official <code>company_tickers_exchange.json</code>. New listings are added,
        reassigned tickers keep their history, and delisted ones are marked inactive (never
        deleted). Requires <code>SEC_USER_AGENT</code>.
      </p>
      {sync.isError && <ErrorNotice error={sync.error} title="Sync failed" />}
      {sync.data && (
        <dl className="kv-grid">
          {Object.entries(sync.data).map(([key, value]) => (
            <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{String(value)}</dd></div>
          ))}
        </dl>
      )}
    </Panel>
  );
}

function Users({ currentUserId }: { currentUserId?: string }) {
  const queryClient = useQueryClient();
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: api.adminUsers });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { role?: Role; is_active?: boolean } }) =>
      api.updateUser(id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin"] }),
  });
  return (
    <Panel kicker="ACCESS CONTROL" title="Users" className="admin-wide">
      {users.isPending && <InlineState>Loading users…</InlineState>}
      {users.isError && <ErrorNotice error={users.error} />}
      {update.isError && <ErrorNotice error={update.error} title="Update failed" />}
      {users.data && (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Last sign-in</th></tr></thead>
            <tbody>
              {users.data.map((user) => (
                <tr key={user.id}>
                  <td><strong>{user.display_name}</strong><small>{user.email}</small></td>
                  <td>
                    <select aria-label={`Role for ${user.email}`} disabled={update.isPending}
                      onChange={(e) => update.mutate({ id: user.id, body: { role: e.target.value as Role } })}
                      value={user.role}>
                      {ROLES.map((role) => <option key={role} value={role}>{role}</option>)}
                    </select>
                  </td>
                  <td>
                    <button className="button-link" disabled={update.isPending || user.id === currentUserId}
                      onClick={() => update.mutate({ id: user.id, body: { is_active: !user.is_active } })}
                      type="button">
                      <StatusPill tone={user.is_active ? "ok" : "off"}>{user.is_active ? "ACTIVE" : "DISABLED"}</StatusPill>
                    </button>
                  </td>
                  <td>{formatDateTime(user.last_login_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="panel-copy">Role or status changes sign the user out of every session immediately.</p>
    </Panel>
  );
}

function ProviderLog() {
  const fetches = useQuery({ queryKey: ["admin", "fetches"], queryFn: api.providerFetches });
  return (
    <Panel kicker="OBSERVABILITY" title="Provider requests" className="admin-wide">
      {fetches.isError && <ErrorNotice error={fetches.error} />}
      {fetches.data?.length === 0 && <InlineState>No provider requests recorded yet.</InlineState>}
      {fetches.data && fetches.data.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>When</th><th>Provider</th><th>Dataset</th><th>Outcome</th><th className="num">Rows</th><th className="num">Latency</th></tr></thead>
            <tbody>
              {fetches.data.map((fetch) => (
                <tr key={fetch.id}>
                  <td>{formatDateTime(fetch.retrieved_at)}</td>
                  <td>{fetch.provider}</td>
                  <td>{fetch.dataset}{fetch.subject && <small>{fetch.subject}</small>}</td>
                  <td>
                    <StatusPill tone={fetch.status === "success" ? "ok" : "warn"}>
                      {fetch.status === "success" ? "OK" : fetch.error_code ?? "ERROR"}
                    </StatusPill>
                  </td>
                  <td className="num">{fetch.record_count ?? "—"}{fetch.rejected_count ? ` (−${fetch.rejected_count})` : ""}</td>
                  <td className="num">{fetch.latency_ms} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function AuditLog() {
  const events = useQuery({ queryKey: ["admin", "audit"], queryFn: api.auditEvents });
  return (
    <Panel kicker="SECURITY" title="Audit trail" className="admin-wide">
      {events.isError && <ErrorNotice error={events.error} />}
      {events.data && (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>When</th><th>Action</th><th>Outcome</th><th>Request</th></tr></thead>
            <tbody>
              {events.data.map((event) => (
                <tr key={event.id}>
                  <td>{formatDateTime(event.occurred_at)}</td>
                  <td>{event.action}{event.resource_type && <small>{event.resource_type} {event.resource_id}</small>}</td>
                  <td><StatusPill tone={event.outcome === "success" ? "ok" : "warn"}>{event.outcome.toUpperCase()}</StatusPill></td>
                  <td className="mono">{event.request_id?.slice(0, 8) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
