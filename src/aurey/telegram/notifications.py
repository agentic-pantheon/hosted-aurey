"""Proactive Telegram DMs (e.g. peer transfer received)."""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from sqlalchemy import select

from aurey.cloud.hosted_access import format_telegram_handle
from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.peer_transfer_context import (
    clear_peer_transfer_recipient,
    current_peer_transfer_recipient,
)
from aurey.cloud.signing_context import current_hosted_telegram_user_id
from aurey.service.state import AureyServiceState
from aurey.telegram.client import resolve_telegram_bot_token

_log = logging.getLogger(__name__)

__all__ = [
    "NotifyResult",
    "TransferNotifyCallback",
    "build_transfer_received_html",
    "notify_telegram_user",
    "schedule_transfer_received_notify",
]


@dataclass(frozen=True)
class NotifyResult:
    delivered: bool
    detail: str | None = None


async def notify_telegram_user(
    *,
    bot_token: str,
    telegram_user_id: int,
    html: str,
) -> NotifyResult:
    from telegram import Bot
    from telegram.error import BadRequest, Forbidden

    try:
        async with Bot(token=bot_token) as bot:
            await bot.send_message(
                chat_id=telegram_user_id,
                text=html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        return NotifyResult(delivered=True)
    except Forbidden:
        return NotifyResult(
            delivered=False,
            detail="User has not started the bot or blocked it.",
        )
    except BadRequest as exc:
        return NotifyResult(delivered=False, detail=str(exc))
    except Exception as exc:
        _log.warning("notify_telegram_user failed tid=%s: %s", telegram_user_id, exc)
        return NotifyResult(delivered=False, detail=str(exc))


def build_transfer_received_html(*, sender_handle: str, tx_hash: str | None) -> str:
    short = ""
    if tx_hash:
        h = tx_hash.strip()
        if len(h) > 14:
            short = f"{h[:8]}…{h[-6:]}"
        else:
            short = h
    who = html.escape(sender_handle, quote=False)
    tx_line = f" Tx: <code>{html.escape(short, quote=False)}</code>." if short else "."
    return f"<b>{who}</b> sent you a transfer on Aurey.{tx_line}"


def _sender_display_handle(state: AureyServiceState, sender_tid: int) -> str:
    factory = state.hosted_session_factory
    if factory is None:
        return "An Aurey user"
    db = factory()
    try:
        row = db.scalar(
            select(HostedPlatformUserORM).where(
                HostedPlatformUserORM.telegram_user_id == sender_tid,
            ),
        )
        if row is None:
            return "An Aurey user"
        return format_telegram_handle(
            telegram_username=row.telegram_username,
            telegram_user_id=row.telegram_user_id,
        )
    finally:
        db.close()


def schedule_transfer_received_notify(
    state: AureyServiceState,
    *,
    loop: asyncio.AbstractEventLoop,
    sender_telegram_user_id: int,
    recipient_telegram_user_id: int,
    recipient_handle: str,
    tx_hash: str | None,
) -> None:
    token = resolve_telegram_bot_token(state)
    sender_handle = _sender_display_handle(state, sender_telegram_user_id)
    html = build_transfer_received_html(sender_handle=sender_handle, tx_hash=tx_hash)

    async def _run() -> None:
        result = await notify_telegram_user(
            bot_token=token,
            telegram_user_id=recipient_telegram_user_id,
            html=html,
        )
        if not result.delivered:
            _log.info(
                "transfer notify not delivered recipient=%s handle=%s detail=%s",
                recipient_telegram_user_id,
                recipient_handle,
                result.detail,
            )

    try:
        asyncio.run_coroutine_threadsafe(_run(), loop)
    except Exception:
        _log.warning("schedule_transfer_received_notify failed", exc_info=True)


class TransferNotifyCallback(BaseCallbackHandler):
    """After successful ``tx_execute``, DM the resolved peer recipient (Telegram bot only)."""

    def __init__(
        self,
        *,
        state: AureyServiceState,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._state = state
        self._loop = loop
        self._last_tool_name: str | None = None

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        self._last_tool_name = serialized.get("name") if isinstance(serialized, dict) else None
        return None

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        name = self._last_tool_name or ""
        if name != "tx_execute":
            return None
        if not isinstance(output, dict) or not output.get("ok"):
            return None
        result = output.get("result")
        if not isinstance(result, dict):
            return None
        tx_hash = result.get("tx_hash")
        if isinstance(tx_hash, str):
            tx_hash_out: str | None = tx_hash.strip() or None
        else:
            tx_hash_out = None

        peer = current_peer_transfer_recipient.get()
        sender_tid = current_hosted_telegram_user_id.get()
        if peer is None or sender_tid is None:
            return None

        # One-shot: a resolved recipient is notified at most once per turn so a
        # later unrelated tx_execute (e.g. a swap) does not re-DM the same person.
        clear_peer_transfer_recipient()

        schedule_transfer_received_notify(
            self._state,
            loop=self._loop,
            sender_telegram_user_id=int(sender_tid),
            recipient_telegram_user_id=peer.telegram_user_id,
            recipient_handle=peer.telegram_handle,
            tx_hash=tx_hash_out,
        )
        return None
