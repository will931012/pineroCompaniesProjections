"use client";

import { useQuery } from "@tanstack/react-query";
import { Database, LineChart } from "lucide-react";
import { api } from "@/lib/api/endpoints";
import { formatDateTime } from "@/lib/format";
import { ErrorNotice, InlineState, Panel, StatusPill } from "./ui";

export function useSystemStatus() {
  return useQuery({ queryKey: ["system-status"], queryFn: api.systemStatus, refetchInterval: 60_000 });
}

export function SystemStatusPanel() {
  const status = useSystemStatus();
  return (
    <Panel kicker="SYSTEM STATUS" title="Data connections">
      {status.isPending && <InlineState>Checking providers…</InlineState>}
      {status.isError && <ErrorNotice error={status.error} title="Status unavailable" />}
      {status.data?.providers.map((provider) => {
        const Icon = provider.kind === "market_data" ? LineChart : Database;
        const failing = provider.last_error_at && (!provider.last_success_at
          || provider.last_error_at > provider.last_success_at);
        return (
          <div className="connection-row" key={provider.key}>
            <span className={`connection-icon${provider.configured ? " connection-icon-green" : ""}`}>
              <Icon size={17} />
            </span>
            <span className="connection-copy">
              <strong>{provider.kind === "market_data" ? "Market data" : "SEC EDGAR"}
                {provider.name && provider.kind === "market_data" ? ` · ${provider.name}` : ""}</strong>
              <small>
                {provider.configured
                  ? provider.last_success_at
                    ? `Last success ${formatDateTime(provider.last_success_at)}`
                    : "Configured · no successful request yet"
                  : provider.message}
              </small>
              {failing && <small className="connection-error">Last error: {provider.last_error_code}</small>}
            </span>
            <StatusPill tone={!provider.configured ? "off" : failing ? "warn" : "ok"}>
              {!provider.configured ? "NOT CONFIGURED" : failing ? "DEGRADED" : "READY"}
            </StatusPill>
          </div>
        );
      })}
      <div className="connection-note">
        <span className="note-marker" />
        <span>Values stay blank until verified source data exists. Nothing is estimated or filled in.</span>
      </div>
    </Panel>
  );
}
