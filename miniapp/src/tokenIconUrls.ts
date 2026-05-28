/** PNG icon URLs for common tickers (fallback when Zerion omits ``icon_url``). */

const SYMBOL_PNG: Record<string, string> = {
  ETH: "https://assets.coingecko.com/coins/images/279/small/ethereum.png",
  WETH: "https://assets.coingecko.com/coins/images/2518/small/weth.png",
  USDC: "https://assets.coingecko.com/coins/images/6319/small/usdc.png",
  USDT: "https://assets.coingecko.com/coins/images/325/small/Tether.png",
  DAI: "https://assets.coingecko.com/coins/images/9956/small/Badge_Dai.png",
  WBTC: "https://assets.coingecko.com/coins/images/7598/small/wrapped_bitcoin_wbtc.png",
  BTC: "https://assets.coingecko.com/coins/images/1/small/bitcoin.png",
  SOL: "https://assets.coingecko.com/coins/images/4128/small/solana.png",
  LINK: "https://assets.coingecko.com/coins/images/877/small/chainlink-new-logo.png",
  ARB: "https://assets.coingecko.com/coins/images/16547/small/arb.jpg",
  OP: "https://assets.coingecko.com/coins/images/25244/small/Optimism.png",
  MATIC: "https://assets.coingecko.com/coins/images/4713/small/polygon.png",
  POL: "https://assets.coingecko.com/coins/images/4713/small/polygon.png",
  BNB: "https://assets.coingecko.com/coins/images/825/small/bnb-icon2_2x.png",
  AVAX: "https://assets.coingecko.com/coins/images/12559/small/Avalanche_Circle_RedWhite_Trans.png",
  GHO: "https://assets.coingecko.com/coins/images/30663/small/gho-token-logo.png",
  CBETH: "https://assets.coingecko.com/coins/images/27008/small/cbeth.png",
  WSTETH: "https://assets.coingecko.com/coins/images/18834/small/wstETH.png",
  RETH: "https://assets.coingecko.com/coins/images/20764/small/reth.png",
  EURC: "https://assets.coingecko.com/coins/images/26045/small/euro.png",
};

export function resolveTokenIconUrl(symbol: string | null | undefined, iconUrl?: string | null): string | null {
  const fromApi = iconUrl?.trim();
  if (fromApi && fromApi.toLowerCase().startsWith("https://")) {
    return fromApi;
  }
  const sym = (symbol || "").trim().toUpperCase();
  if (!sym) return null;
  return SYMBOL_PNG[sym] ?? null;
}
