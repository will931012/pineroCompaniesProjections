import { SERIES_COLORS } from "./LineChart";

/** A small unlabeled trend line; the numbers beside it carry the meaning. */
export function Sparkline({ values, width = 96, height = 28, label }: {
  values: number[];
  width?: number;
  height?: number;
  label: string;
}) {
  if (values.length < 2) return <span className="sparkline-empty">—</span>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * (width - 2) + 1},${height - 1 - ((v - min) / span) * (height - 2)}`)
    .join(" ");
  return (
    <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      <polyline points={points} fill="none" stroke={SERIES_COLORS[0]} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

export type CurveSeries = { name: string; points: { months: number; value: number; tenor: string }[] };

const W = 520;
const H = 220;
const PAD = { left: 40, right: 12, top: 12, bottom: 28 };

/** Yield by maturity on a log-spaced axis, so short tenors are not crowded together. */
export function YieldCurveChart({ curves }: { curves: CurveSeries[] }) {
  const all = curves.flatMap((c) => c.points);
  if (all.length === 0) return null;
  const x = (months: number) => PAD.left + (Math.log(months) / Math.log(360)) * (W - PAD.left - PAD.right);
  const lo = Math.min(...all.map((p) => p.value));
  const hi = Math.max(...all.map((p) => p.value));
  const yMin = Math.floor(lo * 200) / 200;
  const yMax = Math.ceil(hi * 200) / 200;
  const y = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - PAD.top - PAD.bottom);
  const ticks = [1, 3, 12, 24, 60, 120, 360];
  const tickLabel = (m: number) => (m < 12 ? `${m}M` : `${m / 12}Y`);
  const yTicks = Array.from({ length: 5 }, (_, i) => yMin + ((yMax - yMin) * i) / 4);
  return (
    <figure className="curve-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Treasury par yield curve">
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} stroke="#f0f3ef" />
            <text x={PAD.left - 6} y={y(v) + 3} textAnchor="end" className="axis-label">{(v * 100).toFixed(2)}%</text>
          </g>
        ))}
        {ticks.map((m) => (
          <text key={m} x={x(m)} y={H - 8} textAnchor="middle" className="axis-label">{tickLabel(m)}</text>
        ))}
        {curves.map((c, i) => (
          <g key={c.name}>
            <polyline fill="none" stroke={SERIES_COLORS[i]} strokeWidth="2"
              points={c.points.map((p) => `${x(p.months)},${y(p.value)}`).join(" ")} />
            {c.points.map((p) => (
              <circle key={p.tenor} cx={x(p.months)} cy={y(p.value)} r="4" fill={SERIES_COLORS[i]} stroke="#fff" strokeWidth="2">
                <title>{`${c.name} · ${p.tenor}: ${(p.value * 100).toFixed(2)}%`}</title>
              </circle>
            ))}
          </g>
        ))}
      </svg>
      <figcaption className="chart-legend">
        {curves.map((c, i) => <span key={c.name}><i style={{ background: SERIES_COLORS[i] }} />{c.name}</span>)}
      </figcaption>
    </figure>
  );
}

export type XYSeries = { name: string; points: [number, number][]; dashed?: boolean; markers?: boolean };

/** Lines over a numeric x-axis (e.g. days since an event), optionally on a log y-axis. */
export function XYChart({ series, xLabel, yFormat, logY = false, height = 260, label, xTicks, yTicks: yTickValues }: {
  series: XYSeries[];
  xLabel: string;
  yFormat: (v: number) => string;
  logY?: boolean;
  height?: number;
  label: string;
  xTicks?: number[];
  yTicks?: number[];
}) {
  const width = 640;
  const pad = { left: 52, right: 28, top: 12, bottom: 36 };
  const points = series.flatMap((s) => s.points).filter(([, y]) => Number.isFinite(y) && (!logY || y > 0));
  if (points.length === 0) return null;
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const t = (v: number) => (logY ? Math.log10(v) : v);
  const [y0, y1] = [t(Math.min(...ys)), t(Math.max(...ys))];
  const sx = (v: number) => pad.left + ((v - x0) / (x1 - x0 || 1)) * (width - pad.left - pad.right);
  const sy = (v: number) => pad.top + (1 - (t(v) - y0) / (y1 - y0 || 1)) * (height - pad.top - pad.bottom);
  const ticksX = xTicks ?? Array.from({ length: 5 }, (_, i) => x0 + ((x1 - x0) * i) / 4);
  const ticksY = yTickValues ?? (logY
    ? Array.from({ length: Math.floor(y1) - Math.ceil(y0) + 1 }, (_, i) => 10 ** (Math.ceil(y0) + i))
    : Array.from({ length: 5 }, (_, i) => y0 + ((y1 - y0) * i) / 4));
  return (
    <figure className="xy-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
        {ticksY.map((v) => (
          <g key={v}>
            <line x1={pad.left} x2={width - pad.right} y1={sy(v)} y2={sy(v)} stroke="#f0f3ef" />
            <text x={pad.left - 6} y={sy(v) + 3} textAnchor="end" className="axis-label">{yFormat(v)}</text>
          </g>
        ))}
        {ticksX.map((v) => (
          <text key={v} x={sx(v)} y={height - 18} textAnchor="middle" className="axis-label">{Math.round(v * 100) / 100}</text>
        ))}
        <text x={(width + pad.left) / 2} y={height - 3} textAnchor="middle" className="axis-label">{xLabel}</text>
        {series.map((s, i) => (
          <g key={s.name}>
            <polyline fill="none" stroke={SERIES_COLORS[i % SERIES_COLORS.length]} strokeWidth="2"
              strokeDasharray={s.dashed ? "4 4" : undefined}
              points={s.points.filter(([, y]) => !logY || y > 0).map(([x, y]) => `${sx(x)},${sy(y)}`).join(" ")} />
            {s.markers && s.points.map(([x, y]) => (
              <circle key={`${x}-${y}`} cx={sx(x)} cy={sy(y)} r="4" fill={SERIES_COLORS[i % SERIES_COLORS.length]} stroke="#fff" strokeWidth="2">
                <title>{`${s.name}: ${Math.round(x * 1000) / 1000} → ${yFormat(y)}`}</title>
              </circle>
            ))}
          </g>
        ))}
      </svg>
      {series.length > 1 && (
        <figcaption className="chart-legend">
          {series.map((s, i) => <span key={s.name}><i style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} />{s.name}</span>)}
        </figcaption>
      )}
    </figure>
  );
}
