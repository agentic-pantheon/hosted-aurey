type Point = { ts: number; usd: string };

type Props = {
  title: string;
  points: Point[];
};

function parseUsd(s: string): number {
  const n = Number.parseFloat(s);
  return Number.isFinite(n) ? n : 0;
}

export function BalanceChart({ title, points }: Props): JSX.Element | null {
  if (points.length < 2) return null;

  const values = points.map((p) => parseUsd(p.usd));
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const span = maxV - minV || 1;

  const w = 320;
  const h = 120;
  const padX = 8;
  const padY = 12;
  const innerW = w - padX * 2;
  const innerH = h - padY * 2;

  const coords = points.map((p, i) => {
    const x = padX + (i / (points.length - 1)) * innerW;
    const v = parseUsd(p.usd);
    const y = padY + innerH - ((v - minV) / span) * innerH;
    return { x, y, v };
  });

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
      <div className="subtle">{title}</div>
      <svg viewBox={`0 0 ${w} ${h}`} className="balance-chart" aria-hidden>
        <polyline
          fill="none"
          stroke="var(--tg-button-color, #5865f2)"
          strokeWidth="2"
          points={poly}
        />
        <circle cx={last.x} cy={last.y} r="3" fill="var(--tg-button-color, #5865f2)" />
      </svg>
      <div className="chart-range">
        <span>{fmt.format(first.v)}</span>
        <span>{fmt.format(last.v)}</span>
      </div>
    </div>
  );
}
