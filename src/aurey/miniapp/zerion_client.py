"""Zerion REST client for Mini App portfolio visualization (read-only)."""

from __future__ import annotations

import base64
import logging
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from aurey.graphs.chains import chain_info
from aurey.graphs.ports import HttpJsonPort, HttpJsonRequestError

_log = logging.getLogger(__name__)

ZERION_API_BASE = "https://api.zerion.io/v1"
ZERION_API_HOST = "api.zerion.io"

ALLOWED_ZERION_CHART_PERIODS = frozenset({"day", "week", "month", "year", "max"})


def normalize_chart_period(raw: str | None) -> str:
    """Zerion wallet chart slug (``GET .../charts/{period}``)."""

    period = (raw or "month").strip().lower()
    if period in ALLOWED_ZERION_CHART_PERIODS:
        return period
    return "month"


def is_allowed_zerion_api_url(url: str) -> bool:
    """Only follow Zerion pagination links on the official API host."""

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == ZERION_API_HOST


def _coerce_zerion_next_url(raw: str | None) -> str | None:
    if raw is None:
        return None
    nxt = raw.strip()
    if not nxt or not is_allowed_zerion_api_url(nxt):
        return None
    return nxt

# Aurey slug -> Zerion ``relationships.chain.id`` (identity when omitted).
_SLUG_TO_ZERION_CHAIN: dict[str, str] = {
    "bsc": "binance-smart-chain",
    "polygon": "polygon",
}

# Zerion chain id -> Aurey slug when we have a catalog entry or alias.
_ZERION_CHAIN_TO_SLUG: dict[str, str] = {
    "binance-smart-chain": "bsc",
    "polygon-pos": "polygon",
}


def zerion_authorization_header(api_key: str) -> str:
    """``Authorization: Basic base64(api_key:)`` per Zerion docs."""

    raw = f"{api_key.strip()}:"
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def slug_to_zerion_chain_id(slug: str) -> str:
    key = slug.strip().lower()
    return _SLUG_TO_ZERION_CHAIN.get(key, key)


def zerion_chain_id_to_slug(chain_id: str | None) -> str | None:
    if chain_id is None:
        return None
    zid = str(chain_id).strip().lower()
    if not zid:
        return None
    mapped = _ZERION_CHAIN_TO_SLUG.get(zid)
    if mapped is not None:
        return mapped
    if chain_info(zid) is not None:
        return zid
    return zid


def _decimal_from_any(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d


def _wallet_path(wallet: str) -> str:
    return quote(wallet.strip(), safe="")


def zerion_http_error_message(err: HttpJsonRequestError) -> str:
    payload = err.payload
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                detail = first.get("detail") or first.get("title")
                if detail:
                    return str(detail)
        if payload.get("message"):
            return str(payload["message"])
    if err.body_text.strip():
        return err.body_text.strip()[:500]
    return str(err)


def _json_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": zerion_authorization_header(api_key),
        "Accept": "application/json",
        "User-Agent": "Aurey/1.0",
    }


def _zerion_get(
    http: HttpJsonPort,
    *,
    api_key: str,
    url: str,
) -> dict[str, Any]:
    payload = http.request_json(
        method="GET",
        url=url,
        headers=_json_headers(api_key),
        json_body=None,
    )
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected Zerion JSON root type: {type(payload).__name__}")
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        title = str(first.get("title") or first.get("detail") or "zerion_error")
        raise HttpJsonRequestError(
            status_code=502,
            body_text=str(errors)[:2000],
            payload={"message": title, "errors": errors},
        )
    return payload


def _build_query(params: dict[str, str]) -> str:
    if not params:
        return ""
    return "?" + urlencode(params, doseq=True)


def fetch_wallet_portfolio(
    http: HttpJsonPort,
    *,
    api_key: str,
    wallet_address: str,
    currency: str = "usd",
    chain_slugs: tuple[str, ...] = (),
) -> dict[str, Any]:
    """GET /v1/wallets/{address}/portfolio"""

    params: dict[str, str] = {"currency": currency}
    if chain_slugs:
        zids = [slug_to_zerion_chain_id(c) for c in chain_slugs]
        params["filter[chain_ids]"] = ",".join(zids)
    url = f"{ZERION_API_BASE}/wallets/{_wallet_path(wallet_address)}/portfolio{_build_query(params)}"
    return _zerion_get(http, api_key=api_key, url=url)


def fetch_wallet_fungible_positions(
    http: HttpJsonPort,
    *,
    api_key: str,
    wallet_address: str,
    currency: str = "usd",
    chain_slugs: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """GET /v1/wallets/{address}/positions/ (all pages)."""

    params: dict[str, str] = {"currency": currency}
    if chain_slugs:
        zids = [slug_to_zerion_chain_id(c) for c in chain_slugs]
        params["filter[chain_ids]"] = ",".join(zids)

    base = (
        f"{ZERION_API_BASE}/wallets/{_wallet_path(wallet_address)}/positions/"
        f"{_build_query(params)}"
    )
    out: list[dict[str, Any]] = []
    url: str | None = base
    pages = 0
    while url is not None and pages < 32:
        pages += 1
        payload = _zerion_get(http, api_key=api_key, url=url)
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    out.append(item)
        links = payload.get("links")
        next_url = None
        if isinstance(links, dict):
            next_url = _coerce_zerion_next_url(links.get("next"))
        url = next_url
    return out


def fetch_wallet_balance_chart(
    http: HttpJsonPort,
    *,
    api_key: str,
    wallet_address: str,
    chart_period: str = "month",
    currency: str = "usd",
    chain_slugs: tuple[str, ...] = (),
) -> dict[str, Any]:
    """GET /v1/wallets/{address}/charts/{chart_period}"""

    period = normalize_chart_period(chart_period)
    params: dict[str, str] = {"currency": currency}
    if chain_slugs:
        zids = [slug_to_zerion_chain_id(c) for c in chain_slugs]
        params["filter[chain_ids]"] = ",".join(zids)
    url = (
        f"{ZERION_API_BASE}/wallets/{_wallet_path(wallet_address)}/charts/{quote(period, safe='')}"
        f"{_build_query(params)}"
    )
    return _zerion_get(http, api_key=api_key, url=url)


def parse_portfolio_summary(payload: dict[str, Any]) -> tuple[Decimal | None, dict[str, Decimal]]:
    """Return (total_usd, by_zerion_chain_id -> usd)."""

    data = payload.get("data")
    attrs: dict[str, Any] = {}
    if isinstance(data, dict):
        raw_attrs = data.get("attributes")
        if isinstance(raw_attrs, dict):
            attrs = raw_attrs
    elif isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            raw_attrs = first.get("attributes")
            if isinstance(raw_attrs, dict):
                attrs = raw_attrs

    total_dec: Decimal | None = None
    total_block = attrs.get("total")
    if isinstance(total_block, dict):
        for key in ("positions", "value", "amount"):
            total_dec = _decimal_from_any(total_block.get(key))
            if total_dec is not None:
                break
    if total_dec is None:
        total_dec = _decimal_from_any(attrs.get("total"))

    by_chain: dict[str, Decimal] = {}
    dist = attrs.get("positions_distribution_by_chain")
    if isinstance(dist, dict):
        for z_chain, raw_val in dist.items():
            if not isinstance(z_chain, str):
                continue
            dec = _decimal_from_any(raw_val)
            if dec is not None and dec > 0:
                by_chain[z_chain.strip().lower()] = dec
    return total_dec, by_chain


def parse_balance_chart_points(payload: dict[str, Any]) -> list[tuple[int, Decimal]]:
    data = payload.get("data")
    attrs: dict[str, Any] = {}
    if isinstance(data, dict):
        raw_attrs = data.get("attributes")
        if isinstance(raw_attrs, dict):
            attrs = raw_attrs
    elif isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            raw_attrs = first.get("attributes")
            if isinstance(raw_attrs, dict):
                attrs = raw_attrs

    points_raw = attrs.get("points")
    if not isinstance(points_raw, list):
        return []

    out: list[tuple[int, Decimal]] = []
    for pt in points_raw:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            ts = int(pt[0])
        except (TypeError, ValueError):
            continue
        val = _decimal_from_any(pt[1])
        if val is None:
            continue
        out.append((ts, val))
    out.sort(key=lambda x: x[0])
    return out


def _relationship_resource_id(node: Any) -> str | None:
    """JSON:API relationship id (direct or nested under ``data``)."""

    if not isinstance(node, dict):
        return None
    rid = node.get("id")
    if isinstance(rid, str) and rid.strip():
        return rid.strip()
    data = node.get("data")
    if isinstance(data, dict):
        did = data.get("id")
        if isinstance(did, str) and did.strip():
            return did.strip()
    return None


def _chain_slug_from_implementations(fungible_info: dict[str, Any]) -> str | None:
    impls = fungible_info.get("implementations")
    if not isinstance(impls, list) or not impls:
        return None
    chain_ids: set[str] = set()
    for impl in impls:
        if not isinstance(impl, dict):
            continue
        cid = impl.get("chain_id")
        if cid is None:
            continue
        s = str(cid).strip().lower()
        if s:
            chain_ids.add(s)
    if len(chain_ids) != 1:
        return None
    return zerion_chain_id_to_slug(next(iter(chain_ids)))


def _fungible_icon_url(fungible_info: dict[str, Any]) -> str | None:
    icon = fungible_info.get("icon")
    if isinstance(icon, dict):
        url = icon.get("url")
        if isinstance(url, str) and url.strip().lower().startswith("https://"):
            return url.strip()
    if isinstance(icon, str) and icon.strip().lower().startswith("https://"):
        return icon.strip()
    return None


def _token_address_for_chain(fungible_info: dict[str, Any], zerion_chain_id: str) -> str | None:
    impls = fungible_info.get("implementations")
    if not isinstance(impls, list):
        return None
    zc = zerion_chain_id.strip().lower()
    for impl in impls:
        if not isinstance(impl, dict):
            continue
        if str(impl.get("chain_id") or "").strip().lower() != zc:
            continue
        addr = str(impl.get("address") or "").strip()
        return addr if addr else None
    return None


def _protocol_label(attrs: dict[str, Any], relationships: dict[str, Any]) -> str | None:
    proto = attrs.get("protocol")
    if isinstance(proto, str) and proto.strip():
        return proto.strip()
    if isinstance(proto, dict):
        for key in ("name", "display_name", "id"):
            v = proto.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    dapp = relationships.get("dapp")
    if isinstance(dapp, dict):
        did = dapp.get("id")
        if isinstance(did, str) and did.strip():
            return did.strip()
    name = attrs.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def position_is_trash(attrs: dict[str, Any], fungible_info: dict[str, Any]) -> bool:
    flags = attrs.get("flags")
    if isinstance(flags, dict) and flags.get("is_trash") is True:
        return True
    fflags = fungible_info.get("flags")
    if isinstance(fflags, dict) and fflags.get("is_trash") is True:
        return True
    return False


def position_is_verified(fungible_info: dict[str, Any]) -> bool:
    fflags = fungible_info.get("flags")
    return isinstance(fflags, dict) and fflags.get("verified") is True


def parse_position_row(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one Zerion ``positions`` resource."""

    attrs = item.get("attributes")
    if not isinstance(attrs, dict):
        return None
    rel = item.get("relationships")
    relationships = rel if isinstance(rel, dict) else {}

    chain_rel = relationships.get("chain")
    zerion_chain = _relationship_resource_id(chain_rel) if chain_rel is not None else None
    z_chain_s = str(zerion_chain).strip().lower() if zerion_chain is not None else ""

    fungible = attrs.get("fungible_info")
    fungible_info = fungible if isinstance(fungible, dict) else {}

    chain_slug = zerion_chain_id_to_slug(z_chain_s) if z_chain_s else None
    if chain_slug is None:
        chain_slug = _chain_slug_from_implementations(fungible_info)

    quantity = attrs.get("quantity")
    q = quantity if isinstance(quantity, dict) else {}
    balance = q.get("numeric")
    if balance is None:
        balance = q.get("float")
    balance_s = str(balance).strip() if balance is not None else None

    usd = _decimal_from_any(attrs.get("value"))
    pos_type = str(attrs.get("position_type") or "wallet").strip().lower() or "wallet"

    sym_raw = fungible_info.get("symbol")
    symbol = str(sym_raw).strip() if sym_raw is not None and str(sym_raw).strip() else None
    name_raw = fungible_info.get("name")
    name = str(name_raw).strip() if name_raw is not None and str(name_raw).strip() else None

    if not z_chain_s and chain_slug:
        z_chain_s = slug_to_zerion_chain_id(chain_slug).strip().lower()

    token_address = _token_address_for_chain(fungible_info, z_chain_s) if z_chain_s else None

    return {
        "chain": chain_slug,
        "zerion_chain": z_chain_s or None,
        "position_type": pos_type,
        "symbol": symbol,
        "name": name,
        "balance_decimal": balance_s,
        "usd_value": usd,
        "token_address": token_address,
        "protocol_name": _protocol_label(attrs, relationships),
        "pool_address": attrs.get("pool_address"),
        "is_trash": position_is_trash(attrs, fungible_info),
        "zerion_verified": position_is_verified(fungible_info),
        "icon_url": _fungible_icon_url(fungible_info),
    }


__all__ = [
    "ALLOWED_ZERION_CHART_PERIODS",
    "fetch_wallet_balance_chart",
    "fetch_wallet_fungible_positions",
    "fetch_wallet_portfolio",
    "is_allowed_zerion_api_url",
    "normalize_chart_period",
    "parse_balance_chart_points",
    "parse_portfolio_summary",
    "parse_position_row",
    "position_is_trash",
    "slug_to_zerion_chain_id",
    "zerion_http_error_message",
    "zerion_chain_id_to_slug",
]
