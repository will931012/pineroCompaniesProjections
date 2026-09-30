"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, Play, Trash2 } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useState } from "react";
import { ErrorNotice, PageHeading, Panel, StatusPill, type Tone } from "@/components/ui";
import { api, type AlertRule, type AlertRuleIn } from "@/lib/api/endpoints";
import { formatDateTime } from "@/lib/format";
import { buildRule, describeRule, KINDS, type RuleDraft } from "@/lib/alerts";

const EMAIL_TONE: Record<string, Tone> = {
  sent: "ok", pending: "neutral", failed: "warn", not_configured: "off", disabled: "off",
};

function StatusPanel() {
  const status = useQuery({ queryKey: ["alert-status"], queryFn: api.alertStatus });
  const test = useMutation({ mutationFn: api.testAlertEmail });
  const data = status.data;
  return (
    <Panel kicker="DELIVERY" title="Email & worker">
      {status.isError && <ErrorNotice error={status.error} title="Status unavailable" />}
      {data && (
        <>
          <dl className="kv-grid">
            <div><dt>Email</dt><dd>{data.email_configured ? `On (${data.email_provider})` : "Not configured"}</dd></div>
            <div><dt>Sends to</dt><dd>{data.recipient}</dd></div>
            <div><dt>From</dt><dd>{data.email_from}</dd></div>
          </dl>
          {!data.email_configured && (
            <p className="quality-warning">
              Alerts are recorded and listed below, but no email is sent until RESEND_API_KEY is set on the API and worker.
            </p>
          )}
          {data.email_configured && (
            <div className="index-controls">
              <button className="button-secondary" disabled={test.isPending} onClick={() => test.mutate()} type="button">
                <Mail size={14} /> {test.isPending ? "Sending…" : "Send a test email"}
              </button>
              {test.isSuccess && <small>Sent to {data.recipient}.</small>}
              {test.isError && <ErrorNotice error={test.error} title="Test email failed" />}
            </div>
          )}
          <ul className="availability">
            {data.jobs.map((job) => (
              <li key={job.kind} title={job.last_error ?? JSON.stringify(job.result ?? {})}>
                <span>{job.kind.replaceAll("_", " ")}</span>
                <StatusPill tone={job.status === "done" ? "ok" : job.status === "failed" ? "warn" : "off"}>
                  {job.status === "never" ? "NEVER RUN" : `${job.status.toUpperCase()} · ${formatDateTime(job.finished_at)}`}
                </StatusPill>
              </li>
            ))}
          </ul>
          <p className="panel-copy">The background worker checks filings every 30 minutes, news every hour, and rules every 5 minutes.</p>
        </>
      )}
    </Panel>
  );
}

function RuleBuilder() {
  const queryClient = useQueryClient();
  const types = useQuery({ queryKey: ["event-types"], queryFn: api.eventTypes, staleTime: Infinity });
  const watchlists = useQuery({ queryKey: ["watchlists"], queryFn: api.watchlists });
  const [draft, setDraft] = useState<RuleDraft>({
    name: "", kind: "news", tickers: "", watchlistId: "", forms: ["8-K"], eventTypes: [],
    firstOnly: true, codes: ["P"], minValue: "", minMovePct: "5", email: true,
  });
  const create = useMutation({
    mutationFn: (body: AlertRuleIn) => api.createAlertRule(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alert-rules"] });
      setDraft({ ...draft, name: "" });
    },
  });
  const set = (patch: Partial<RuleDraft>) => setDraft({ ...draft, ...patch });
  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(buildRule(draft));
  }

  return (
    <Panel kicker="NEW RULE" title="Create an alert">
      <form className="rule-form" onSubmit={submit}>
        <label>Name<input required maxLength={120} value={draft.name} placeholder="e.g. NVDA earnings"
          onChange={(e) => set({ name: e.target.value })} /></label>
        <label>Type
          <select value={draft.kind} onChange={(e) => set({ kind: e.target.value as RuleDraft["kind"] })}>
            {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
          </select>
        </label>
        <label>Tickers<input value={draft.tickers} placeholder="AAPL, MSFT"
          onChange={(e) => set({ tickers: e.target.value })} /></label>
        <label>Watchlist
          <select value={draft.watchlistId} onChange={(e) => set({ watchlistId: e.target.value })}>
            <option value="">None</option>
            {(watchlists.data ?? []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>

        {draft.kind === "filing" && (
          <fieldset><legend>Forms</legend>
            {["10-K", "10-Q", "8-K", "DEF 14A", "S-1", "SC 13D"].map((form) => (
              <label className="check" key={form}>
                <input type="checkbox" checked={draft.forms.includes(form)} onChange={() => set({ forms: toggle(draft.forms, form) })} />
                {form}
              </label>
            ))}
          </fieldset>
        )}
        {draft.kind === "news" && (
          <fieldset><legend>Event types (none selected = all)</legend>
            {(types.data ?? []).filter((t) => t.key !== "other").map((t) => (
              <label className="check" key={t.key}>
                <input type="checkbox" checked={draft.eventTypes.includes(t.key)}
                  onChange={() => set({ eventTypes: toggle(draft.eventTypes, t.key) })} />
                {t.label}
              </label>
            ))}
            <label className="check">
              <input type="checkbox" checked={draft.firstOnly} onChange={(e) => set({ firstOnly: e.target.checked })} />
              First report of a story only
            </label>
          </fieldset>
        )}
        {draft.kind === "insider" && (
          <fieldset><legend>Form 4 trades</legend>
            {[["P", "Open-market purchases"], ["S", "Open-market sales"]].map(([code, label]) => (
              <label className="check" key={code}>
                <input type="checkbox" checked={draft.codes.includes(code)} onChange={() => set({ codes: toggle(draft.codes, code) })} />
                {label}
              </label>
            ))}
            <label>Minimum value (USD)<input inputMode="decimal" value={draft.minValue} placeholder="100000"
              onChange={(e) => set({ minValue: e.target.value })} /></label>
          </fieldset>
        )}
        {draft.kind === "price" && (
          <label>Daily move of at least (%)<input inputMode="decimal" value={draft.minMovePct}
            onChange={(e) => set({ minMovePct: e.target.value })} /></label>
        )}
        <label className="check">
          <input type="checkbox" checked={draft.email} onChange={(e) => set({ email: e.target.checked })} /> Send by email
        </label>
        <button className="button-primary" disabled={create.isPending} type="submit">
          {create.isPending ? "Creating…" : "Create alert"}
        </button>
      </form>
      {create.isError && <ErrorNotice error={create.error} title="Could not create the alert" />}
      <p className="panel-copy">
        A rule only fires for things found after it was created, so loading history never floods your inbox.
        {draft.kind === "price" && " Price alerts need a market-data provider (Tiingo)."}
      </p>
    </Panel>
  );
}

function RulesList() {
  const queryClient = useQueryClient();
  const rules = useQuery({ queryKey: ["alert-rules"], queryFn: api.alertRules });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["alert-rules"] });
    queryClient.invalidateQueries({ queryKey: ["alerts"] });
  };
  const update = useMutation({
    mutationFn: (rule: AlertRule) => api.updateAlertRule(rule.id, { active: !rule.active }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: api.deleteAlertRule, onSuccess: refresh });
  const evaluate = useMutation({ mutationFn: api.evaluateAlertRule, onSuccess: refresh });
  return (
    <Panel kicker="RULES" title="Your alerts" className="admin-wide">
      {rules.isError && <ErrorNotice error={rules.error} title="Rules unavailable" />}
      {rules.data && rules.data.length === 0 && <p className="panel-copy">No alert rules yet.</p>}
      {rules.data && rules.data.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Rule</th><th>Watches</th><th>Last checked</th><th /></tr></thead>
            <tbody>
              {rules.data.map((rule) => (
                <tr key={rule.id} className={rule.active ? undefined : "row-muted"}>
                  <td><b>{rule.name}</b><small>{describeRule(rule)}{rule.email ? " · email" : ""}</small></td>
                  <td>{[rule.tickers.join(", "), rule.watchlist_name && `watchlist ${rule.watchlist_name}`].filter(Boolean).join(" · ")}
                    <small>{rule.companies} companies</small></td>
                  <td className="mono">{rule.evaluated_at ? formatDateTime(rule.evaluated_at) : "not yet"}</td>
                  <td className="rule-actions">
                    <button className="button-link" onClick={() => update.mutate(rule)} type="button">{rule.active ? "Pause" : "Resume"}</button>
                    <button className="icon-button" aria-label="Check now" title="Check now" onClick={() => evaluate.mutate(rule.id)} type="button"><Play size={14} /></button>
                    <button className="icon-button" aria-label="Delete" title="Delete" onClick={() => remove.mutate(rule.id)} type="button"><Trash2 size={14} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {evaluate.data && <p className="panel-copy">Checked: {evaluate.data.created} new alerts, {evaluate.data.sent} emailed.</p>}
    </Panel>
  );
}

function History() {
  const queryClient = useQueryClient();
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: api.alerts });
  const read = useMutation({
    mutationFn: () => api.markAlertsRead(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });
  const data = alerts.data;
  return (
    <Panel kicker={data ? `${data.unread} UNREAD` : "HISTORY"} title="Triggered alerts" className="admin-wide"
      action={data && data.unread > 0 ? <button className="button-link" onClick={() => read.mutate()} type="button">Mark all read</button> : undefined}>
      {alerts.isError && <ErrorNotice error={alerts.error} title="Alerts unavailable" />}
      {data && data.alerts.length === 0 && <p className="panel-copy">Nothing has triggered yet.</p>}
      {data && data.alerts.length > 0 && (
        <ol className="alert-list">
          {data.alerts.map((alert) => (
            <li key={alert.id} className={alert.read ? undefined : "unread"}>
              <div className="event-meta">
                <span className="event-time">{formatDateTime(alert.created_at)}</span>
                <span className="event-type">{alert.rule_name}</span>
                <span title={alert.email_error ?? undefined}>
                  <StatusPill tone={EMAIL_TONE[alert.email_status] ?? "neutral"}>EMAIL {alert.email_status.replace("_", " ").toUpperCase()}</StatusPill>
                </span>
              </div>
              <b>{alert.link && alert.link.startsWith("/") ? <Link href={alert.link}>{alert.title}</Link> : alert.title}</b>
              <p>{alert.body}</p>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

export default function AlertsPage() {
  return (
    <>
      <PageHeading kicker="ALERTS" title="Alerts"
        subtitle="Email when a watched company files, reports earnings, makes news, trades insider shares, or moves sharply." />
      <div className="admin-grid">
        <RuleBuilder />
        <StatusPanel />
        <RulesList />
        <History />
      </div>
    </>
  );
}
