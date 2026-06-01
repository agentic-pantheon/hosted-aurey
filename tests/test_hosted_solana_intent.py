"""Tests for Solana wallet question detection."""

from __future__ import annotations

from aurey.cloud.hosted_solana_intent import message_asks_hosted_solana_wallet


def test_detects_solana_address_question() -> None:
    assert message_asks_hosted_solana_wallet("What's my Solana address?")
    assert message_asks_hosted_solana_wallet("what is my solana wallet")
    assert message_asks_hosted_solana_wallet("show me my solana wallet address")


def test_ignores_unrelated_messages() -> None:
    assert not message_asks_hosted_solana_wallet("how much USDC on Base?")
    assert not message_asks_hosted_solana_wallet("swap eth to usdc")
