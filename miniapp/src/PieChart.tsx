/** Simple SVG donut chart (no external chart library). */

export type PieSlice = {
  label: string;
  value: number;
  color: string;
};

const PALETTE = [
  "#6366f1",
  "#a855f7",
  "#ec4899",
  "#f59e0b",
  "#10b981",
  "#3b82f6",
  "#ef4444",
  "#14b8a6",
  "#8b5cf6",
  "#64748b",
];

export function paletteColor(index: number): string {
  return PALETTE[index % PALETTE.length];
}

function polar(cx: number, cy: number, r: number, angleRad: number): { x: number; y: number } {
  return { x: cx + r * Math.cos(angleRad), y: cy + r * Math.sin(angleRad) };
}

function slicePath(
  cx: number,
  cy: number,
  r: number,
  start: number,
  end: number,
): string {
  const s = polar(cx, cy, r, start);
  const e = polar(cx, cy, r, end);
  const large = end - start > Math.PI ? 1 : 0;
  return `M ${cx} ${cy} L ${s.x} ${s.y} A ${r} ${r} 0 ${large} 1 ${e.x} ${e.y} Z`;
}

type Props = {
  title: string;
  slices: PieSlice[];
  size?: number;
};

export function PieChart({ title, slices, size = 168 }: Props): JSX.Element {
  const positive = slices.filter((s) => s.value > 0);
  const total = positive.reduce((a, s) => a + s.value, 0);

  if (total <= 0) {
    return (
      <div className="chart-card">
        <div className="chart-title">{title}</div>
        <div className="subtle">No priced holdings to chart.</div>
      </div>
    );
  }

  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 4;
  let angle = -Math.PI / 2;

  const paths = positive.map((sl) => {
    const frac = sl.value / total;
    const sweep = frac * Math.PI * 2;
    const start = angle;
    angle += sweep;
    return { ...sl, d: slicePath(cx, cy, r, start, angle), pct: frac * 100 };
  });

  return (
    <div className="chart-card">
      <div className="chart-title">{title}</div>
      <div className="chart-row">
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={title}>
          {paths.map((p) => (
            <path key={p.label} d={p.d} fill={p.color} stroke="var(--tg-bg-color, #0f0f14)" strokeWidth={1} />
          ))}
          <circle cx={cx} cy={cy} r={r * 0.52} fill="var(--tg-bg-color, #0f0f14)" />
        </svg>
        <ul className="chart-legend">
          {paths.map((p) => (
            <li key={p.label}>
              <span className="swatch" style={{ background: p.color }} />
              <span className="legend-label">{p.label}</span>
              <span className="legend-val">{p.pct.toFixed(1)}%</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function topSlicesByValue(
  entries: { label: string; value: number }[],
  maxSlices = 8,
): PieSlice[] {
  const sorted = [...entries].filter((e) => e.value > 0).sort((a, b) => b.value - a.value);
  if (sorted.length <= maxSlices) {
    return sorted.map((e, i) => ({ ...e, color: paletteColor(i) }));
  }
  const head = sorted.slice(0, maxSlices - 1);
  const tailSum = sorted.slice(maxSlices - 1).reduce((a, e) => a + e.value, 0);
  const out: PieSlice[] = head.map((e, i) => ({ ...e, color: paletteColor(i) }));
  out.push({ label: "Other", value: tailSum, color: paletteColor(maxSlices - 1) });
  return out;
}
