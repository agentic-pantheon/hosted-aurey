import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BalanceChart } from "./BalanceChart";
import { chartRainbowColor } from "./chartColors";
import { PieChart, topSlicesByValue } from "./PieChart";
import {
  isPortfolioCacheFresh,
  readPortfolioCache,
  writePortfolioCache,
} from "./portfolioCache";
import { fetchPortfolioSnapshot, zerionWalletUrl, type ChartPeriod } from "./portfolioFetch";
import { applyAppTheme } from "./theme";
import { TokenIcon } from "./TokenIcon";
import { usePullToRefresh } from "./usePullToRefresh";

type PortfolioTokenRow = {
  chain: string;
  symbol: string | null;
  name: string | null;
  balance_decimal: string | null;
  usd_value: string | null;
  token_address: string | null;
  curated?: boolean;
  icon_url?: string | null;
};

type PortfolioTokenAggregated = {
  asset_key: string;
  symbol: string;
  name: string | null;
  chains: string[];
  balance_decimal: string;
  usd_value: string | null;
  curated: boolean;
  icon_url?: string | null;
};

type PortfolioDefi = {
  chain_id?: number | null;
  chain: string | null;
  protocol_name: string | null;
  symbol: string | null;
  vault_address: string | null;
  balance_usd: string | null;
  balance_native: string | null;
};

type SummaryByChain = { chain: string; usd: string | null };
type PortfolioError = {
  source: string;
  chain?: string | null;
  code?: string | null;
  message?: string | null;
};

type PortfolioBalanceChart = {
  period: string;
  points: { ts: number; usd: string }[];
};

type PortfolioSnap = {
  wallet_address: string;
  summary: {
    total_usd?: string | null;
    by_chain: SummaryByChain[];
  };
  tokens: PortfolioTokenRow[];
  tokens_aggregated?: PortfolioTokenAggregated[];
  defi: PortfolioDefi[];
  balance_chart?: PortfolioBalanceChart | null;
  errors: PortfolioError[];
  chains_available: string[];
};

type Tab = "overview" | "tokens" | "defi";

const CHART_PERIOD_OPTIONS: { id: ChartPeriod; label: string }[] = [
  { id: "day", label: "1D" },
  { id: "week", label: "1W" },
  { id: "month", label: "1M" },
  { id: "year", label: "1Y" },
  { id: "max", label: "All" },
];

const USD_FMT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function parseUsd(s: string | null | undefined): number {
  if (!s) return 0;
  const n = Number.parseFloat(s);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function formatUsd(n: number): string {
  if (n <= 0) return "—";
  return USD_FMT.format(n);
}

function formatChainSlug(slug: string): string {
  const s = slug.trim().toLowerCase();
  if (!s || s === "unknown") return "Unknown";
  const aliases: Record<string, string> = {
    bsc: "BSC",
    arbitrum: "Arbitrum",
    optimism: "Optimism",
    ethereum: "Ethereum",
    base: "Base",
    polygon: "Polygon",
    monad: "Monad",
  };
  return aliases[s] ?? s.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function tabLabel(t: Tab): string {
  if (t === "overview") return "Overview";
  if (t === "tokens") return "Tokens";
  return "DeFi";
}

function tokenComposition(
  aggregated: PortfolioTokenAggregated[],
  defi: PortfolioDefi[],
): { label: string; value: number }[] {
  const byKey = new Map<string, number>();

  for (const t of aggregated) {
    const usd = parseUsd(t.usd_value);
    if (usd <= 0) continue;
    byKey.set(t.symbol.toUpperCase(), (byKey.get(t.symbol.toUpperCase()) || 0) + usd);
  }

  for (const d of defi) {
    const usd = parseUsd(d.balance_usd);
    if (usd <= 0) continue;
    const proto = d.protocol_name || "DeFi";
    const sym = d.symbol || "position";
    const chain = d.chain || "unknown";
    const key = `${proto}: ${sym} (${chain})`;
    byKey.set(key, (byKey.get(key) || 0) + usd);
  }

  return [...byKey.entries()].map(([label, value]) => ({ label, value }));
}

function chainComposition(
  tokens: PortfolioTokenRow[],
  defi: PortfolioDefi[],
  byChainSummary: SummaryByChain[],
): { label: string; value: number }[] {
  const fromSummary = byChainSummary
    .map((r) => ({ label: r.chain, value: parseUsd(r.usd) }))
    .filter((x) => x.value > 0);
  if (fromSummary.length > 0) return fromSummary;

  const byChain = new Map<string, number>();
  for (const t of tokens) {
    const usd = parseUsd(t.usd_value);
    if (usd > 0) byChain.set(t.chain, (byChain.get(t.chain) || 0) + usd);
  }
  for (const d of defi) {
    const usd = parseUsd(d.balance_usd);
    const c = d.chain || "unknown";
    if (usd > 0) byChain.set(c, (byChain.get(c) || 0) + usd);
  }
  return [...byChain.entries()].map(([label, value]) => ({ label, value }));
}

function formatFetchedAt(ms: number): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(ms));
}

function telegramUserId(): number | undefined {
  return window.Telegram?.WebApp?.initDataUnsafe?.user?.id;
}

export default function App(): JSX.Element {
  const wa = window.Telegram?.WebApp;
  const [tab, setTab] = useState<Tab>("overview");
  const [chartPeriod, setChartPeriod] = useState<ChartPeriod>("month");
  const [chainSel, setChainSel] = useState<string>("all");
  const [showUnverified, setShowUnverified] = useState(false);
  const [snapshot, setSnapshot] = useState<PortfolioSnap | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [fatal, setFatal] = useState<string | null>(null);
  const [detailCode, setDetailCode] = useState<string | null>(null);
  const initDataRef = useRef("");

  const applyFetchResult = useCallback(
    (result: Awaited<ReturnType<typeof fetchPortfolioSnapshot<PortfolioSnap>>>, period: ChartPeriod) => {
      if (result.ok) {
        setSnapshot(result.snapshot);
        setFatal(null);
        setDetailCode(null);
        const at = Date.now();
        setFetchedAt(at);
        writePortfolioCache(telegramUserId(), period, result.snapshot, at);
        return;
      }
      setFatal(result.fatal);
      setDetailCode(result.detailCode);
    },
    [],
  );

  const refreshFromNetwork = useCallback(async () => {
    const init = initDataRef.current;
    if (!init) return;
    const result = await fetchPortfolioSnapshot<PortfolioSnap>(init, chartPeriod);
    applyFetchResult(result, chartPeriod);
  }, [applyFetchResult, chartPeriod]);

  const { pullOffset, refreshing, handlers: pullHandlers } = usePullToRefresh({
    onRefresh: refreshFromNetwork,
    disabled: loading || Boolean(fatal && !snapshot),
  });

  useEffect(() => {
    applyAppTheme();
    wa?.expand();
  }, [wa]);

  useEffect(() => {
    let cancelled = false;
    async function boot() {
      const init =
        typeof window.Telegram !== "undefined" ? window.Telegram.WebApp.initData || "" : "";
      initDataRef.current = init;
      if (!init) {
        if (!cancelled) {
          setFatal(
            "Open this App from Aurey Telegram (portfolio menu or /portfolio); local browser has no Telegram context.",
          );
          setLoading(false);
        }
        return;
      }

      const uid = telegramUserId();
      const cached = readPortfolioCache<PortfolioSnap>(uid, chartPeriod);
      const cacheFresh = cached !== null && isPortfolioCacheFresh(cached.fetchedAt);

      if (cacheFresh && !cancelled) {
        setSnapshot(cached.snapshot);
        setFetchedAt(cached.fetchedAt);
        setLoading(false);
      }

      try {
        const result = await fetchPortfolioSnapshot<PortfolioSnap>(init, chartPeriod);
        if (cancelled) return;
        if (!result.ok) {
          if (cacheFresh && cached !== null && !cancelled) {
            setSnapshot(cached.snapshot);
            setFetchedAt(cached.fetchedAt);
            setFatal(null);
            setDetailCode(null);
          } else {
            applyFetchResult(result, chartPeriod);
          }
        } else {
          applyFetchResult(result, chartPeriod);
        }
      } catch (e) {
        if (!cancelled) {
          if (cacheFresh && cached !== null) {
            setSnapshot(cached.snapshot);
            setFetchedAt(cached.fetchedAt);
            setFatal(null);
            setDetailCode(null);
          } else {
            setFatal(e instanceof Error ? e.message : "Network error");
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, [applyFetchResult, chartPeriod]);

  const chains = useMemo(() => snapshot?.chains_available ?? [], [snapshot]);

  useEffect(() => {
    if (chainSel !== "all" && !chains.includes(chainSel)) setChainSel("all");
  }, [chains, chainSel]);

  const aggregatedTokens = useMemo((): PortfolioTokenAggregated[] => {
    if (!snapshot) return [];
    if (snapshot.tokens_aggregated && snapshot.tokens_aggregated.length > 0) {
      return snapshot.tokens_aggregated;
    }
    const map = new Map<string, PortfolioTokenAggregated>();
    for (const t of snapshot.tokens) {
      const sym = (t.symbol || "?").toUpperCase();
      const prev = map.get(sym);
      const usd = parseUsd(t.usd_value);
      const bal = t.balance_decimal || "0";
      if (!prev) {
        map.set(sym, {
          asset_key: sym,
          symbol: sym,
          name: t.name,
          chains: [t.chain],
          balance_decimal: bal,
          usd_value: usd > 0 ? String(usd) : null,
          curated: t.curated ?? false,
          icon_url: t.icon_url ?? null,
        });
      } else {
        const chains = new Set([...prev.chains, t.chain]);
        const totalBal = Number.parseFloat(prev.balance_decimal) + Number.parseFloat(bal);
        const totalUsd = parseUsd(prev.usd_value) + usd;
        map.set(sym, {
          ...prev,
          chains: [...chains].sort(),
          balance_decimal: String(totalBal),
          usd_value: totalUsd > 0 ? String(totalUsd) : prev.usd_value,
          curated: prev.curated && (t.curated ?? false),
          icon_url: prev.icon_url ?? t.icon_url ?? null,
        });
      }
    }
    return [...map.values()];
  }, [snapshot]);

  const filteredAggregated = useMemo(() => {
    return aggregatedTokens.filter((t) => {
      if (!showUnverified && !t.curated) return false;
      if (chainSel !== "all" && !t.chains.includes(chainSel)) return false;
      return true;
    });
  }, [aggregatedTokens, showUnverified, chainSel]);

  const filteredDefi =
    snapshot?.defi.filter((p) => chainSel === "all" || p.chain === chainSel) ?? [];

  const pricedTotal = useMemo(() => {
    if (!snapshot) return 0;
    let sum = 0;
    for (const t of snapshot.tokens) sum += parseUsd(t.usd_value);
    for (const d of snapshot.defi) sum += parseUsd(d.balance_usd);
    return sum;
  }, [snapshot]);

  const curatedAggregated = useMemo(
    () => aggregatedTokens.filter((t) => t.curated),
    [aggregatedTokens],
  );

  const tokenPie = useMemo(() => {
    if (!snapshot) return [];
    const entries = tokenComposition(curatedAggregated, snapshot.defi);
    return topSlicesByValue(entries);
  }, [snapshot, curatedAggregated]);

  const chainPie = useMemo(() => {
    if (!snapshot) return [];
    const entries = chainComposition(
      snapshot.tokens,
      snapshot.defi,
      snapshot.summary.by_chain,
    );
    return topSlicesByValue(entries);
  }, [snapshot]);

  function renderOverview(): JSX.Element {
    if (!snapshot) return <></>;
    const serverTotal = parseUsd(snapshot.summary.total_usd);
    const headline = formatUsd(serverTotal > 0 ? serverTotal : pricedTotal);

    const barRows = snapshot.summary.by_chain.filter((row) => parseUsd(row.usd) > 0);
    const maxBar = Math.max(...barRows.map((row) => parseUsd(row.usd)), 1);

    const unpricedCount =
      snapshot.tokens.filter((t) => parseUsd(t.usd_value) <= 0 && t.balance_decimal).length +
      snapshot.defi.filter((d) => parseUsd(d.balance_usd) <= 0 && d.balance_native).length;

    return (
      <>
        <div className="card">
          <div className="subtle">Total portfolio (priced holdings only)</div>
          <div className="total">{headline}</div>
          <a
            className="wallet-link wallet-mono"
            href={zerionWalletUrl(snapshot.wallet_address)}
            target="_blank"
            rel="noopener noreferrer"
          >
            {snapshot.wallet_address}
          </a>
          {unpricedCount > 0 ? (
            <div className="subtle" style={{ marginTop: 8 }}>
              {unpricedCount} position(s) have balance but no USD quote — not included in total.
            </div>
          ) : null}
          {barRows.length > 0 && (
            <div style={{ marginTop: "12px" }}>
              {barRows.map((row, barIndex) => {
                const v = parseUsd(row.usd);
                const pct = v > 0 ? Math.round((100 * v) / maxBar) : 0;
                return (
                  <div key={row.chain} className="bar">
                    <span className="name">{formatChainSlug(row.chain)}</span>
                    <div className="track">
                      <div
                        className="fill"
                        style={{
                          width: `${pct}%`,
                          background: chartRainbowColor(barIndex),
                        }}
                      />
                    </div>
                    <small>{formatUsd(v)}</small>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        <BalanceChart
          title="Balance (Zerion)"
          points={snapshot.balance_chart?.points ?? []}
          period={chartPeriod}
          periodOptions={CHART_PERIOD_OPTIONS}
          onPeriodChange={(p) => setChartPeriod(p as ChartPeriod)}
        />
        <PieChart title="By token / position" slices={tokenPie} />
        <PieChart title="By chain" slices={chainPie} />
      </>
    );
  }

  function renderTokens(): JSX.Element {
    if (!filteredAggregated.length) {
      return (
        <div className="subtle">
          {showUnverified
            ? "No token rows for this filter."
            : "No verified tokens — enable “Show unverified tokens” for spam / unknown contracts."}
        </div>
      );
    }
    const sorted = [...filteredAggregated].sort(
      (a, b) => parseUsd(b.usd_value) - parseUsd(a.usd_value),
    );
    return (
      <>
        {sorted.map((t) => (
          <div key={t.asset_key} className="card token-card">
            <div className="token-card-head">
              <TokenIcon symbol={t.symbol} iconUrl={t.icon_url} size={40} />
              <div className="token-card-title">
                <strong>
                  {t.symbol}
                  {!t.curated ? (
                    <span className="badge-unverified"> unverified</span>
                  ) : null}
                </strong>
                <div className="chain-pills">
                  {t.chains.map((c) => (
                    <span key={c} className="chain-pill">
                      {formatChainSlug(c)}
                    </span>
                  ))}
                </div>
              </div>
            </div>
            {t.name ? <div className="token-card-name">{t.name}</div> : null}
            <div className="row-muted">
              {t.balance_decimal}
              {" · "}
              {parseUsd(t.usd_value) > 0 ? formatUsd(parseUsd(t.usd_value)) : "unpriced"}
            </div>
          </div>
        ))}
      </>
    );
  }

  function renderDefi(): JSX.Element {
    if (!filteredDefi.length) return <div className="subtle">No Earn / DeFi rows.</div>;
    const byProto = new Map<string, { proto: string; rows: PortfolioDefi[] }>();
    for (const r of filteredDefi) {
      const k = r.protocol_name || "Unknown protocol";
      if (!byProto.has(k)) byProto.set(k, { proto: k, rows: [] });
      byProto.get(k)!.rows.push(r);
    }
    return (
      <>
        {[...byProto.values()].map((grp) => (
          <section key={grp.proto}>
            <div className="group-title">{grp.proto}</div>
            {grp.rows.map((row, ix) => (
              <div key={`${row.chain_id ?? "?"}:${row.vault_address ?? ix}`} className="card">
                <strong>
                  {row.symbol ?? "?"}
                  {" · "}
                  <small className="row-muted">{row.chain ?? `chain-${row.chain_id ?? "?"}`}</small>
                </strong>
                <div className="row-muted">
                  {parseUsd(row.balance_usd) > 0 ? formatUsd(parseUsd(row.balance_usd)) : ""}
                  {row.balance_native ? ` (${row.balance_native} native)` : ""}
                </div>
              </div>
            ))}
          </section>
        ))}
      </>
    );
  }

  function errorBanner(): JSX.Element | null {
    if (!snapshot?.errors?.length) return null;
    return (
      <div className="warning">
        Some data providers failed partially:{" "}
        {snapshot.errors.map((e) => [e.chain, e.code].filter(Boolean).join(" ") || e.source).join(
          "; ",
        )}
      </div>
    );
  }

  function body(): JSX.Element {
    if (loading) return <div className="layout skeleton">Loading…</div>;
    if (fatal || !snapshot)
      return (
        <div className="layout error">
          {detailCode === "wallet_not_ready" ? (
            <p>
              Wallet not provisioned yet. Finish <strong>/start</strong> onboarding in Aurey Telegram
              to fund your wallet.
            </p>
          ) : (
            fatal || "Unavailable"
          )}
        </div>
      );
    const tabsList: Tab[] = ["overview", "tokens", "defi"];

    return (
      <div
        className="pull-root"
        {...pullHandlers}
        style={{ touchAction: pullOffset > 0 ? "none" : "auto" }}
      >
        <div
          className="pull-indicator"
          style={{
            height: `${Math.max(pullOffset, refreshing ? 36 : 0)}px`,
            opacity: pullOffset > 8 || refreshing ? 1 : 0,
          }}
          aria-live="polite"
        >
          {refreshing ? "Refreshing…" : pullOffset > 48 ? "Release to refresh" : "Pull to refresh"}
        </div>
        <div
          className="layout pull-content"
          style={{ transform: pullOffset > 0 ? `translateY(${pullOffset}px)` : undefined }}
        >
        <header>
          <div className="title">Portfolio</div>
          <div className="subtle">
            Chains: {chains.join(", ") || "—"}
            {fetchedAt !== null ? ` · Updated ${formatFetchedAt(fetchedAt)}` : ""}
          </div>
        </header>
        {errorBanner()}
        <div className="tabs">
          {tabsList.map((t) => (
            <button
              key={t}
              type="button"
              data-active={tab === t ? "1" : "0"}
              onClick={() => setTab(t)}
            >
              {tabLabel(t)}
            </button>
          ))}
        </div>
        {tab === "tokens" ? (
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={showUnverified}
              onChange={(e) => setShowUnverified(e.target.checked)}
            />
            Show unverified tokens
          </label>
        ) : null}
        {tab !== "overview" ? (
          <div className="chips">
            <button
              type="button"
              data-active={chainSel === "all" ? "1" : "0"}
              onClick={() => setChainSel("all")}
            >
              All chains
            </button>
            {chains.map((c) => (
              <button
                key={c}
                type="button"
                data-active={chainSel === c ? "1" : "0"}
                onClick={() => setChainSel(c)}
              >
                {c === "all" ? "All chains" : formatChainSlug(c)}
              </button>
            ))}
          </div>
        ) : null}

        {tab === "overview" ? renderOverview() : null}
        {tab === "tokens" ? renderTokens() : null}
        {tab === "defi" ? renderDefi() : null}
        </div>
      </div>
    );
  }

  return body();
}
