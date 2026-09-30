"use client";

import { ExternalLink, FileText } from "lucide-react";
import Link from "next/link";
import type { TimelineEvent } from "@/lib/api/endpoints";
import { formatDateTime } from "@/lib/format";

/** How the event was classified, in words (shown on hover). */
export function evidenceText(event: TimelineEvent): string {
  const evidence = event.evidence as Record<string, unknown>;
  const lines = [
    event.source_kind === "filing"
      ? `SEC ${event.filing?.form ?? "8-K"} items: ${(evidence.items as string[] | undefined)?.join(", ") || "none"}`
      : `Headline matched ${evidence.matched ? `“${String(evidence.matched)}”` : "no event rule"}`,
    event.source_kind === "news" ? `Linked by ${String(evidence.link_method ?? "").replace("_", " ")}` : null,
    event.cluster_size > 1 ? `${event.cluster_size} reports of this story` : null,
  ];
  return lines.filter(Boolean).join("\n");
}

export function EventRow({ event, ticker }: { event: TimelineEvent; ticker?: string }) {
  const title = event.filing ? (
    <Link href={event.filing.app_path}>{event.title}</Link>
  ) : event.news ? (
    <a href={event.news.url} rel="noreferrer noopener" target="_blank">
      {event.title} <ExternalLink size={11} />
    </a>
  ) : (
    event.title
  );
  return (
    <li className={`event-row event-${event.source_kind}`} title={evidenceText(event)}>
      <span className="event-time">{formatDateTime(event.occurred_at)}</span>
      <div className="event-body">
        <div className="event-meta">
          {ticker && <b className="event-ticker">{ticker}</b>}
          <span className="event-type">{event.label}</span>
          <span className="event-source">
            {event.source_kind === "filing" ? (
              <><FileText size={11} /> SEC {event.filing?.form}</>
            ) : (
              event.news?.domain
            )}
          </span>
          {event.novelty === "repeat" && <span className="event-repeat">repeat coverage</span>}
          {event.novelty === "first" && event.outlets.length > 1 && (
            <span className="event-outlets" title={event.outlets.join(", ")}>
              +{event.outlets.length - 1} more outlet{event.outlets.length > 2 ? "s" : ""}
            </span>
          )}
        </div>
        <span className="event-title">{title}</span>
      </div>
    </li>
  );
}

export function EventTimeline({ events, tickerOf }: {
  events: TimelineEvent[];
  tickerOf?: (event: TimelineEvent) => string;
}) {
  return (
    <ol className="event-timeline">
      {events.map((event) => <EventRow event={event} key={event.id} ticker={tickerOf?.(event)} />)}
    </ol>
  );
}
