import { CircleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { isApiError } from "@/lib/api/client";
import type { SourceRef } from "@/lib/api/endpoints";
import { formatDateTime } from "@/lib/format";

export function PageHeading({ kicker, title, subtitle, aside }: {
  kicker: string;
  title: string;
  subtitle?: string;
  aside?: ReactNode;
}) {
  return (
    <section className="page-heading">
      <div>
        <div className="eyebrow"><span className="eyebrow-line" />{kicker}</div>
        <h1>{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {aside}
    </section>
  );
}

export function Panel({ kicker, title, action, children, className = "" }: {
  kicker: string;
  title: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <article className={`panel ${className}`}>
      <div className="panel-heading">
        <div><span className="section-kicker">{kicker}</span><h2>{title}</h2></div>
        {action}
      </div>
      {children}
    </article>
  );
}

export type Tone = "ok" | "warn" | "off" | "neutral";

export function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`status-pill status-pill-${tone}`}><i />{children}</span>;
}

export function InlineState({ tone = "neutral", children }: { tone?: "neutral" | "alert"; children: ReactNode }) {
  return (
    <div className="search-result-state">
      <span className={`result-line${tone === "alert" ? " result-line-alert" : ""}`} />
      {children}
    </div>
  );
}

export function ErrorNotice({ error, title = "Request failed" }: { error: unknown; title?: string }) {
  const message = error instanceof Error ? error.message : "Unexpected error.";
  const requestId = isApiError(error) ? error.requestId : undefined;
  return (
    <div className="error-notice" role="alert">
      <CircleAlert size={16} />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
        {requestId && <small>Request ID {requestId}</small>}
      </div>
    </div>
  );
}

/** Where displayed values came from. Rendered wherever external data is shown. */
export function SourceList({ sources }: { sources: SourceRef[] }) {
  if (sources.length === 0) {
    return <p className="source-empty">No external source recorded for these values.</p>;
  }
  return (
    <ul className="source-list">
      {sources.map((source) => (
        <li key={source.fetch_id}>
          <span className="source-provider">{source.provider}</span>
          <span className="source-dataset">{source.dataset}</span>
          <a href={source.source_url} rel="noreferrer noopener" target="_blank"
            title={source.source_url}>{new URL(source.source_url).hostname}</a>
          <span className="source-time">Retrieved {formatDateTime(source.retrieved_at)}</span>
          {source.license_note && <small>{source.license_note}</small>}
        </li>
      ))}
    </ul>
  );
}
