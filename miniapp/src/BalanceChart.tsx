type Point = { ts: number; usd: string };

export type ChartPeriodOption = { id: string; label: string };

type Props = {
  title: string;
  points: Point[];
  period: string;
  periodOptions: ChartPeriodOption[];
  onPeriodChange: (period: string) => void;
};

function parseUsd(s: string): number {
  const n = Number.parseFloat(s);
  return Number.isFinite(n) ? n : 0;
}

export function BalanceChart({
  title,
  points,
  period,
  periodOptions,
  onPeriodChange,
}: Props): JSX.Element {
  const values = points.map((p) => parseUsd(p.usd));
  const hasSeries = points.length >= 2;
  const minV = hasSeries ? Math.min(...values) : 0;
  const maxV = hasSeries ? Math.max(...values) : 1;
  const span = maxV - minV || 1;

  const w = 320;
  const h = 120;
  const padX = 8;
  const padY = 12;
  const innerW = w - padX * 2;
  const innerH = h - padY * 2;

  const coords = hasSeries
    ? points.map((p, i) => {
        const x = padX + (i / (points.length - 1)) * innerW;
        const v = parseUsd(p.usd);
        const y = padY + innerH - ((v - minV) / span) * innerH;
        return { x, y, v };
      })
    : [];

  const poly = coords.map((c) => `${c.x},${c.y}`).join(" ");
  const last = coords[coords.length - 1];
  const first = coords[0];

  const fmt = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

  return (
    <div className="card">
      <div className="chart-head">
        <div className="subtle">{title}</div>
        <div className="chart-periods" role="group" aria-label="Chart timeframe">
          {periodOptions.map((opt) => (
            <button
              key={opt.id}
              type="button"
              className="chart-period-btn"
              data-active={period === opt.id ? "1" : "0"}
              onClick={() => onPeriodChange(opt.id)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      {hasSeries ? (
        <>
          <svg viewBox={`0 0 ${w} ${h}`} className="balance-chart" aria-hidden>
            <defs>
              <linearGradient
                id="aurey-balance-line"
                gradientUnits="userSpaceOnUse"
                x1={padX}
                y1={0}
                x2={w - padX}
                y2={0}
              >
                <stop offset="0%" stopColor="var(--viz-7)" />
                <stop offset="20%" stopColor="var(--viz-5)" />
                <stop offset="40%" stopColor="var(--viz-9)" />
                <stop offset="60%" stopColor="var(--viz-4)" />
                <stop offset="80%" stopColor="var(--viz-3)" />
                <stop offset="100%" stopColor="var(--viz-2)" />
              </linearGradient>
              <linearGradient id="aurey-balance-area" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--viz-7)" stopOpacity={0.35} />
                <stop offset="100%" stopColor="var(--viz-3)" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            {coords.length >= 2 ? (
              <polygon
                fill="url(#aurey-balance-area)"
                points={`${coords[0].x},${h - padY} ${poly} ${coords[coords.length - 1].x},${h - padY}`}
              />
            ) : null}
            <polyline
              fill="none"
              stroke="url(#aurey-balance-line)"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
              points={poly}
            />
            <circle cx={last.x} cy={last.y} r="3.5" fill="var(--viz-3)" stroke="var(--background)" strokeWidth={1} />
          </svg>
          <div className="chart-range">
            <span>{fmt.format(first.v)}</span>
            <span>{fmt.format(last.v)}</span>
          </div>
        </>
      ) : (
        <div className="subtle" style={{ marginTop: 8 }}>
          No chart data for this timeframe.
        </div>
      )}
    </div>
  );
}
