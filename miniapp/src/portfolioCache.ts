/** Client-side portfolio snapshot cache (Telegram user scoped, 1h TTL). */

export const PORTFOLIO_CACHE_TTL_MS = 60 * 60 * 1000;

const STORAGE_PREFIX = "aurey.miniapp.portfolio.v1";

export type CachedPortfolio<T> = {
  fetchedAt: number;
  snapshot: T;
};

function storageKey(telegramUserId: number | undefined): string {
  const uid = telegramUserId ?? 0;
  return `${STORAGE_PREFIX}:${uid}`;
}

export function readPortfolioCache<T>(
  telegramUserId: number | undefined,
): CachedPortfolio<T> | null {
  try {
    const raw = localStorage.getItem(storageKey(telegramUserId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CachedPortfolio<T>;
    if (
      typeof parsed?.fetchedAt !== "number" ||
      parsed.snapshot === undefined ||
      parsed.snapshot === null
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
  snapshot: T,
  fetchedAt = Date.now(),
): void {
  try {
    const payload: CachedPortfolio<T> = { fetchedAt, snapshot };
    localStorage.setItem(storageKey(telegramUserId), JSON.stringify(payload));
  } catch {
    /* quota / private mode — ignore */
  }
}

export function clearPortfolioCache(telegramUserId: number | undefined): void {
  try {
    localStorage.removeItem(storageKey(telegramUserId));
  } catch {
    /* ignore */
  }
}
