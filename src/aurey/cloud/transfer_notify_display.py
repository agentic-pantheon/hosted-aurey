"""Human-readable fields for peer transfer received Telegram DMs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aurey.graphs.chains import chain_name_for_id
from aurey.graphs.evm_codec import (
    decode_erc20_transfer_amount_wei,
    format_token_units,
    parse_evm_uint,
)
from aurey.graphs.explorers import explorer_tx_url
from aurey.cloud.hosted_transfer_notify_lookup import execute_payload_from_tx_inputs
from aurey.known_addresses.book import lookup_known_token_by_address
from aurey.service.state import AureyServiceState


@dataclass(frozen=True)
class TransferReceivedDisplay:
    chain_label: str
    token_label: str
    amount_text: str
    tx_hash: str | None
    explorer_tx_url: str | None


def _decimals_for_symbol(symbol: str) -> int:
    s = symbol.strip().upper()
    if s in {"USDC", "USDT", "USDC.E", "DAI"}:
        return 6
    if s == "WBTC":
        return 8
    return 18


def _chain_label(chain_id: int) -> str:
    slug = chain_name_for_id(chain_id)
    if slug:
        return slug.replace("_", " ").title()
    return f"Chain {chain_id}"


def _token_label_and_decimals(
    state: AureyServiceState,
    *,
    chain_slug: str,
    token_address: str,
) -> tuple[str, int]:
    hit = lookup_known_token_by_address(chain_slug, token_address)
    if hit is not None:
        return hit.symbol, _decimals_for_symbol(hit.symbol)

    resolver = state.runtime.token_resolver
    if resolver is not None:
        row = resolver._repo.lookup_address(chain_slug.strip().lower(), token_address)
        if row is not None:
            dec = row.decimals if row.decimals is not None else _decimals_for_symbol(row.symbol)
            return row.symbol, int(dec)

    addr = token_address.strip()
    if len(addr) > 14:
        short = f"{addr[:6]}…{addr[-4:]}"
    else:
        short = addr
    return short, 18


def transfer_received_display_from_execute(
    state: AureyServiceState,
    inputs: dict[str, Any],
    *,
    tx_hash: str | None,
) -> TransferReceivedDisplay | None:
    """Build display fields from ``tx_execute`` inputs (prepared envelope)."""

    payload = execute_payload_from_tx_inputs(state, inputs)
    if payload is None:
        return None
    try:
        chain_id = int(payload.get("chain_id"))
    except (TypeError, ValueError):
        return None
    chain_slug = chain_name_for_id(chain_id)
    if chain_slug is None:
        chain_slug = ""

    kind = str(payload.get("kind") or "").strip()
    token_label = "ETH"
    amount_text = ""

    if kind == "native_transfer":
        try:
            raw = parse_evm_uint(str(payload.get("value_hex") or "0x0"))
        except ValueError:
            return None
        amount_text = format_token_units(raw, 18)
    elif kind == "erc20_transfer":
        if not chain_slug:
            return None
        token_addr = str(payload.get("to") or "").strip()
        raw_amt = decode_erc20_transfer_amount_wei(str(payload.get("data") or ""))
        if raw_amt is None:
            return None
        token_label, decimals = _token_label_and_decimals(
            state,
            chain_slug=chain_slug,
            token_address=token_addr,
        )
        amount_text = format_token_units(raw_amt, decimals)
    else:
        return None

    return TransferReceivedDisplay(
        chain_label=_chain_label(chain_id),
        token_label=token_label,
        amount_text=amount_text,
        tx_hash=tx_hash.strip() if isinstance(tx_hash, str) and tx_hash.strip() else None,
        explorer_tx_url=explorer_tx_url(chain_id, tx_hash),
    )


__all__ = ["TransferReceivedDisplay", "transfer_received_display_from_execute"]
