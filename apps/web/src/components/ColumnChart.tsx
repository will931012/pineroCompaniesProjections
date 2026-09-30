"use client";

import { useState } from "react";
import { formatValue, type ValueUnit } from "@/lib/format";

export type ChartPoint = { key: string; label: string; value: number };

// Validated pair (dataviz validator, light surface): passes chroma, CVD, and 3:1 contrast.
const POSITIVE = "#008a78";
const NEGATIVE = "#c4621f";

const WIDTH = 320;
const HEIGHT = 150;
const PAD = { left: 46, right: 4, top: 8, bottom: 20 };
const MAX_BAR = 24;
const RADIUS = 4;

/** Rounded tick values covering [min, max] and zero, about `count` intervals apart. */
export function niceTicks(min: number, max: number, count = 3): number[] {
  const low = Math.min(0, min);
  const high = Math.max(0, max);
  if (low === high) return [0];
  const raw = (high - low) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw;
  const first = Math.floor(low / step);
  const last = Math.ceil(high / step);
  // Multiply rather than accumulate so ticks stay exact multiples of the step.
  return Array.from({ length: last - first + 1 }, (_, i) => Number(((first + i) * step).toPrecision(12)) || 0);
}

/** Column from the baseline with a rounded data end and a square base. */
export function barPath(x: number, width: number, baseline: number, end: number): string {
  const height = Math.abs(end - baseline);
  const r = Math.min(RADIUS, width / 2, height);
  const up = end < baseline; // SVG y grows downward
  const tip = up ? end + r : end - r;
  const sweep = up ? 1 : 0;
  return [
    `M${x},${baseline}`,
    `V${tip}`,
    `A${r},${r} 0 0 ${sweep} ${x + r},${end}`,
    `H${x + width - r}`,
    `A${r},${r} 0 0 ${sweep} ${x + width},${tip}`,
    `V${baseline}`,
    "Z",
  ].join(" ");
}

/** Single-series column chart. The title names the series, so there is no legend. */
export function ColumnChart({ title, points, unit }: { title: string; points: ChartPoint[]; unit: ValueUnit }) {
  const [active, setActive] = useState<number | null>(null);
  if (points.length === 0) return null;

  const ticks = niceTicks(Math.min(...points.map((p) => p.value)), Math.max(...points.map((p) => p.value)));
  const top = ticks[ticks.length - 1];
  const bottom = ticks[0];
  const plotHeight = HEIGHT - PAD.top - PAD.bottom;
  const plotWidth = WIDTH - PAD.left - PAD.right;
  const y = (value: number) => PAD.top + ((top - value) / (top - bottom)) * plotHeight;
  const slot = plotWidth / points.length;
  const barWidth = Math.min(MAX_BAR, Math.max(2, slot - 2 - slot * 0.3));
  const shown = active ?? points.length - 1;
  const readout = points[shown];

  return (
    <figure className="column-chart">
      <figcaption>
        <span>{title}</span>
        <strong>{formatValue(readout.value, unit)}</strong>
        <small>{readout.label}{active === null ? " · latest" : ""}</small>
      </figcaption>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${title} by period`}
        onMouseLeave={() => setActive(null)}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line className={tick === 0 ? "chart-baseline" : "chart-gridline"} x1={PAD.left} x2={WIDTH - PAD.right}
              y1={y(tick)} y2={y(tick)} />
            <text className="chart-tick" x={PAD.left - 6} y={y(tick)} dy="0.32em" textAnchor="end">
              {formatValue(tick, unit)}
            </text>
          </g>
        ))}
        {points.map((point, index) => {
          const x = PAD.left + slot * index + (slot - barWidth) / 2;
          return (
            <g key={point.key}>
              {point.value !== 0 && (
                <path d={barPath(x, barWidth, y(0), y(point.value))}
                  fill={point.value < 0 ? NEGATIVE : POSITIVE}
                  opacity={active === null || active === index ? 1 : 0.45} />
              )}
              {/* The whole slot is the hover/focus target, larger than the painted bar. */}
              <rect className="chart-hit" x={PAD.left + slot * index} y={PAD.top} width={slot} height={plotHeight}
                tabIndex={0} aria-label={`${point.label}: ${formatValue(point.value, unit)}`}
                onMouseEnter={() => setActive(index)} onFocus={() => setActive(index)} onBlur={() => setActive(null)} />
            </g>
          );
        })}
        <text className="chart-tick" x={PAD.left} y={HEIGHT - 4}>{points[0].label}</text>
        {points.length > 1 && (
          <text className="chart-tick" x={WIDTH - PAD.right} y={HEIGHT - 4} textAnchor="end">
            {points[points.length - 1].label}
          </text>
        )}
      </svg>
    </figure>
  );
}
