const API_JSON = "/v1/miniapp/portfolio";

export type ChartPeriod = "day" | "week" | "month" | "year" | "max";

export type PortfolioFetchFailure = {
  ok: false;
  fatal: string;
  detailCode: string | null;
};

export type PortfolioFetchSuccess<T> = {
  ok: true;
  snapshot: T;
};

export type PortfolioFetchResult<T> = PortfolioFetchSuccess<T> | PortfolioFetchFailure;

export async function fetchPortfolioSnapshot<T>(
  initData: string,
  chartPeriod: ChartPeriod = "month",
): Promise<PortfolioFetchResult<T>> {
  const res = await fetch(API_JSON, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: initData, chart_period: chartPeriod }),
  });
  const ct = res.headers.get("content-type") || "";
  let body: Record<string, unknown> = {};
  if (ct.includes("application/json")) {
    body = (await res.json()) as Record<string, unknown>;
  } else if (!res.ok) {
    body = { detail: await res.text() };
  }
  if (!res.ok) {
    const d = body.detail;
    const obj =
      typeof d === "object" && d !== null && !Array.isArray(d) ? (d as Record<string, string>) : null;
    return {
      ok: false,
      fatal: obj?.message || (typeof d === "string" ? d : `${res.status}`),
      detailCode: obj?.code !== undefined ? String(obj.code) : null,
    };
  }
  return { ok: true, snapshot: body as T };
}

export function zerionWalletUrl(walletAddress: string): string {
  const addr = walletAddress.trim();
  return `https://app.zerion.io/${encodeURIComponent(addr)}/overview`;
}
