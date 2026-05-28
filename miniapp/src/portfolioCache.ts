/** Client-side portfolio snapshot cache (Telegram user scoped, 1h TTL). */

import type { ChartPeriod } from "./portfolioFetch";

export const PORTFOLIO_CACHE_TTL_MS = 60 * 60 * 1000;

const STORAGE_PREFIX = "aurey.miniapp.portfolio.v1";

export type CachedPortfolio<T> = {
  fetchedAt: number;
  chartPeriod: ChartPeriod;
  snapshot: T;
};

function storageKey(telegramUserId: number | undefined, chartPeriod: ChartPeriod): string {
  const uid = telegramUserId ?? 0;
  return `${STORAGE_PREFIX}:${uid}:${chartPeriod}`;
}

export function readPortfolioCache<T>(
  telegramUserId: number | undefined,
  chartPeriod: ChartPeriod,
): CachedPortfolio<T> | null {
  try {
    const raw = localStorage.getItem(storageKey(telegramUserId, chartPeriod));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CachedPortfolio<T>;
    if (
      typeof parsed?.fetchedAt !== "number" ||
      parsed.snapshot === undefined ||
      parsed.snapshot === null ||
      parsed.chartPeriod !== chartPeriod
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function isPortfolioCacheFresh(fetchedAt: number, now = Date.now()): boolean {
  return now - fetchedAt < PORTFOLIO_CACHE_TTL_MS;
}

export function writePortfolioCache<T>(
  telegramUserId: number | undefined,
  chartPeriod: ChartPeriod,
  snapshot: T,
  fetchedAt = Date.now(),
): void {
  try {
    const payload: CachedPortfolio<T> = { fetchedAt, chartPeriod, snapshot };
    localStorage.setItem(storageKey(telegramUserId, chartPeriod), JSON.stringify(payload));
  } catch {
    /* quota / private mode — ignore */
  }
}

export function clearPortfolioCache(telegramUserId: number | undefined): void {
  try {
    const uid = telegramUserId ?? 0;
    const prefix = `${STORAGE_PREFIX}:${uid}:`;
    for (let i = localStorage.length - 1; i >= 0; i -= 1) {
      const k = localStorage.key(i);
      if (k?.startsWith(prefix)) localStorage.removeItem(k);
    }
  } catch {
    /* ignore */
  }
}
