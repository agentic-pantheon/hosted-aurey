"""CoinGecko demo API client (deterministic endpoints only; no ``/search``)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from aurey.graphs.ports import HttpJsonPort, HttpJsonRequestError
from aurey.token_registry.platforms import cg_platform_for_chain_slug, chain_slug_for_cg_platform

_log = logging.getLogger(__name__)

COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoClient:
    """Thin wrapper over :class:`~aurey.graphs.ports.HttpJsonPort`."""

    def __init__(self, *, http: HttpJsonPort, api_key: str) -> None:
        self._http = http
        self._headers = {"x-cg-demo-api-key": api_key.strip(), "accept": "application/json"}

    def fetch_top_markets(
        self,
        *,
        per_page: int,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        qs = urlencode(
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": str(per_page),
                "page": str(page),
                "sparkline": "false",
            }
        )
        url = f"{COINGECKO_API_BASE}/coins/markets?{qs}"
        body = self._http.request_json(method="GET", url=url, headers=self._headers)
        if not isinstance(body, list):
            raise TypeError("CoinGecko markets response must be a JSON array.")
        return [x for x in body if isinstance(x, dict)]

    def fetch_coin_platforms(self, coin_id: str) -> dict[str, Any] | None:
        qs = urlencode(
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
            }
        )
        url = f"{COINGECKO_API_BASE}/coins/{coin_id}?{qs}"
        try:
            body = self._http.request_json(method="GET", url=url, headers=self._headers)
        except HttpJsonRequestError as exc:
            if exc.status_code == 404:
                return None
            raise
        return body if isinstance(body, dict) else None

    def fetch_contract_coin(
        self,
        *,
        chain_slug: str,
        contract_address: str,
    ) -> dict[str, Any] | None:
        platform = cg_platform_for_chain_slug(chain_slug)
        if platform is None:
            return None
        addr = contract_address.strip()
        url = f"{COINGECKO_API_BASE}/coins/{platform}/contract/{addr}"
        try:
            body = self._http.request_json(method="GET", url=url, headers=self._headers)
        except HttpJsonRequestError as exc:
            if exc.status_code in (404, 400):
                return None
            raise
        return body if isinstance(body, dict) else None

    @staticmethod
    def platforms_from_coin_detail(coin: dict[str, Any]) -> list[tuple[str, str, int | None, str, str]]:
        """Return ``(chain_slug, address, decimals, symbol, name)`` rows from ``detail_platforms``."""

        out: list[tuple[str, str, int | None, str, str]] = []
        symbol = str(coin.get("symbol") or "").upper()
        name = str(coin.get("name") or symbol)
        detail = coin.get("detail_platforms")
        if not isinstance(detail, dict):
            return out
        for platform_id, row in detail.items():
            if not isinstance(row, dict):
                continue
            slug = chain_slug_for_cg_platform(str(platform_id))
            if slug is None:
                continue
            addr = row.get("contract_address")
            if not isinstance(addr, str) or not addr.strip():
                continue
            dec_raw = row.get("decimal_place")
            decimals: int | None
            if isinstance(dec_raw, (int, float)):
                decimals = int(dec_raw)
            else:
                decimals = None
            out.append((slug, addr.strip(), decimals, symbol, name))
        return out
