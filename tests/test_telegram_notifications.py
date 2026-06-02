"""Tests for proactive Telegram notifications."""

from __future__ import annotations

import pytest

from aurey.cloud.transfer_notify_display import TransferReceivedDisplay
from aurey.telegram.notifications import build_transfer_received_html


def test_build_transfer_received_html_with_explorer_link() -> None:
    display = TransferReceivedDisplay(
        chain_label="Base",
        token_label="USDC",
        amount_text="10.5",
        tx_hash="0x" + "ab" * 32,
        explorer_tx_url="https://basescan.org/tx/0x" + "ab" * 32,
    )
    html = build_transfer_received_html(sender_handle="@alice", display=display)
    assert "@alice" in html
    assert "USDC" in html
    assert "10.5" in html
    assert "Base" in html
    assert 'href="https://basescan.org/tx/' in html
