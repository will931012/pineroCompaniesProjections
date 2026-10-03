"use client";

import {
  ColorType,
  LineSeries,
  PriceScaleMode,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

// Validated categorical order (dataviz validator, light surface): all checks pass.
export const SERIES_COLORS = ["#008a78", "#c4621f", "#5b6fb3", "#a8447d"] as const;

export type LineSeriesInput = {
  name: string;
  points: { date: string; value: number | null }[];
  color?: string;
  width?: 1 | 2 | 3;
};

function toTime(date: string): UTCTimestamp {
  return (Date.parse(`${date}T00:00:00Z`) / 1000) as UTCTimestamp;
}

/** Lines on one shared axis, with a legend; `guides` draws labelled horizontal levels. */
export function LineChart({ series, height = 280, logScale = false, guides = [], format, label }: {
  series: LineSeriesInput[];
  height?: number;
  logScale?: boolean;
  guides?: { value: number; title: string }[];
  format?: (value: number) => string;
  label: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const instance = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#6f7c73",
        fontFamily: "var(--font-geist-sans), sans-serif",
        fontSize: 11,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: "#f0f3ef" } },
      rightPriceScale: {
        borderColor: "#e3e8e3",
        mode: logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
      },
      timeScale: { borderColor: "#e3e8e3" },
      localization: format ? { priceFormatter: format } : undefined,
      crosshair: { horzLine: { labelVisible: true }, vertLine: { labelVisible: true } },
    });
    series.forEach((s, i) => {
      const line = instance.addSeries(LineSeries, {
        color: s.color ?? SERIES_COLORS[i % SERIES_COLORS.length],
        lineWidth: s.width ?? 2,
        priceLineVisible: false,
        lastValueVisible: i === 0,
        title: "",
      });
      line.setData(
        s.points
          .filter((p) => p.value !== null && Number.isFinite(p.value))
          .map((p) => ({ time: toTime(p.date), value: p.value as number })),
      );
      if (i === 0) {
        for (const guide of guides) {
          line.createPriceLine({ price: guide.value, color: "#9aa69e", lineWidth: 1, lineStyle: 2, title: guide.title });
        }
      }
    });
    instance.timeScale().fitContent();
    chart.current = instance;
    return () => {
      instance.remove();
      chart.current = null;
    };
  }, [series, logScale, guides, format]);

  return (
    <figure className="line-chart">
      <div style={{ height }} ref={container} role="img" aria-label={label} />
      {series.length > 1 && (
        <figcaption className="chart-legend">
          {series.map((s, i) => (
            <span key={s.name}><i style={{ background: s.color ?? SERIES_COLORS[i % SERIES_COLORS.length] }} />{s.name}</span>
          ))}
        </figcaption>
      )}
    </figure>
  );
}
