"""Pydantic request bodies for 1Claw Intents API (BYORPC sign-only, etc.)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IntentsSignTransactionRequest(BaseModel):
    """JSON body for ``POST /v1/agents/{id}/transactions/sign`` (sign-only, no broadcast).

    Field names follow 1Claw Intents docs; ``value`` is a decimal ETH string (e.g. ``\"0.1\"``),
    unlike unified ``/sign`` + ``intent_type: transaction`` which uses wei strings.
    """

    model_config = ConfigDict(extra="allow")

    chain: str = Field(min_length=1)
    to: str = Field(min_length=1, description="Recipient ``0x`` address.")
    value: str = Field(default="0", description='Native transfer amount in ETH decimals (e.g. \"0\", \"0.01\").')
    data: str = Field(default="0x")
    signing_key_path: str | None = None
    simulate_first: bool | None = None
    nonce: int | None = None
    gas_limit: int | None = None
    gas_price: str | None = None
    max_fee_per_gas: str | None = None
    max_priority_fee_per_gas: str | None = None

    def body_dict(self) -> dict[str, Any]:
        """Omit unset optional fields."""

        return self.model_dump(mode="json", exclude_none=True)


__all__ = ["IntentsSignTransactionRequest"]
