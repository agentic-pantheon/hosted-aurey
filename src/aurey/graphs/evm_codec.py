"""Minimal ABI encoding helpers (light web3 use for EIP-55)."""

from __future__ import annotations

from web3 import Web3


def normalize_evm_address(addr: str) -> str:
    raw = addr.strip()
    if len(raw) != 42 or not raw.startswith("0x"):
        raise ValueError("EVM address must be 0x-prefixed 20 bytes.")
    body = raw[2:].lower()
    int(body, 16)
    if len(body) != 40:
        raise ValueError("EVM address must be 20 bytes hex.")
    return "0x" + body


def to_checksum_evm_address(addr: str) -> str:
    """Return EIP-55 checksummed ``0x`` address (LiFi Earn vault detail path expects this shape)."""

    return Web3.to_checksum_address(normalize_evm_address(addr))


def _strip_0x(h: str) -> str:
    return h[2:] if h.startswith(("0x", "0X")) else h


def _pad_addr(addr: str) -> str:
    core = _strip_0x(normalize_evm_address(addr))
    return (64 - len(core)) * "0" + core


def _pad_uint256(n: int) -> str:
    if n < 0:
        raise ValueError("uint256 must be non-negative.")
    return f"{n:064x}"


def erc20_transfer_data(to: str, amount_wei: int) -> str:
    return "0xa9059cbb" + _pad_addr(to) + _pad_uint256(amount_wei)


def decode_erc20_transfer_recipient(data: str | None) -> str | None:
    """Return checksummed recipient from standard ``transfer(address,uint256)`` calldata."""

    body = _strip_0x((data or "").strip())
    if not body.startswith("a9059cbb") or len(body) < 8 + 64 + 64:
        return None
    addr_word = body[8 : 8 + 64]
    try:
        return to_checksum_evm_address("0x" + addr_word[-40:])
    except ValueError:
        return None


def erc20_approve_data(spender: str, amount_wei: int) -> str:
    return "0x095ea7b3" + _pad_addr(spender) + _pad_uint256(amount_wei)


def erc20_allowance_calldata(owner: str, spender: str) -> str:
    """ERC-20 ``allowance(address,address)`` — selector keccak256(...)[:4]."""

    return "0xdd62ed3e" + _pad_addr(owner) + _pad_addr(spender)


def erc20_balance_of_calldata(owner: str) -> str:
    """ERC-20 ``balanceOf(address)`` — selector keccak256(...)[:4]."""

    return "0x70a08231" + _pad_addr(owner)


# ERC-20 `decimals()` selector — keccak256("decimals()")[:4]
ERC20_DECIMALS_CALLDATA = "0x313ce567"


def decode_abi_uint256_word(result_hex: str) -> int:
    """Decode one 32-byte ABI word returned from ``eth_call`` (hex string)."""

    raw = (result_hex or "").strip().lower()
    if not raw.startswith("0x") or len(raw) != 66:
        raise ValueError("eth_call result must be a 32-byte ABI word (0x + 64 hex chars).")
    return int(raw, 16)


def normalize_contract_calldata(data: str | None) -> str:
    """Return ``0x``-prefixed lowercase hex for a tx ``data`` field (``eth_call`` / broadcast).

    Rejects non-hex characters and odd-length payloads (incomplete bytes).
    """

    s = (data or "").strip()
    if not s or s == "0x":
        return "0x"
    if not s.startswith("0x"):
        s = "0x" + s
    body = s[2:]
    # Models and log transports sometimes inject whitespace/newlines inside long hex.
    body = "".join(body.split())
    if not body:
        return "0x"
    for c in body:
        if c not in "0123456789abcdefABCDEF":
            raise ValueError(f"calldata contains non-hex character {c!r}")
    if len(body) % 2 != 0:
        raise ValueError("calldata hex has odd length (incomplete bytes)")
    return "0x" + body.lower()


def parse_evm_uint(value: str | int) -> int:
    """Parse an EVM uint surfaced as hex or decimal text."""

    if isinstance(value, int):
        if value < 0:
            raise ValueError("uint value must be non-negative.")
        return value

    raw = value.strip()
    if not raw:
        raise ValueError("uint value must be non-empty.")
    base = 16 if raw.startswith(("0x", "0X")) else 10
    parsed = int(raw, base)
    if parsed < 0:
        raise ValueError("uint value must be non-negative.")
    return parsed


def format_token_units(raw_amount: int, decimals: int) -> str:
    """Format raw token units as an exact base-10 amount string."""

    if raw_amount < 0:
        raise ValueError("raw_amount must be non-negative.")
    if decimals < 0:
        raise ValueError("decimals must be non-negative.")
    if decimals == 0:
        return str(raw_amount)

    whole, fraction = divmod(raw_amount, 10**decimals)
    if fraction == 0:
        return str(whole)
    fraction_text = f"{fraction:0{decimals}d}".rstrip("0")
    return f"{whole}.{fraction_text}"
