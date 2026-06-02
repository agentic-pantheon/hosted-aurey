"""Tests for proactive Telegram notifications."""

from __future__ import annotations

import pytest

from aurey.telegram.notifications import build_transfer_received_html


def test_build_transfer_received_html_short_hash() -> None:
    html = build_transfer_received_html(
        sender_handle="@alice",
        tx_hash="0xabcdef1234567890abcdef1234567890abcdef12",
    )
    assert "@alice" in html
    assert "0xabcdef" in html or "abcdef" in html
