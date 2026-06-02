"""Proactive Telegram DMs (e.g. peer transfer received)."""

from __future__ import annotations

import asyncio
import html
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from sqlalchemy import select

from aurey.cloud.hosted_access import format_telegram_handle
from aurey.cloud.hosted_transfer_notify_lookup import (
    lookup_peer_recipient_by_wallet,
    recipient_evm_from_transfer_execute,
)
from aurey.cloud.models import HostedPlatformUserORM
from aurey.cloud.peer_transfer_context import (
    PeerTransferRecipient,
    clear_peer_transfer_recipient,
    current_peer_transfer_recipient,
    set_peer_transfer_recipient,
)
from aurey.cloud.signing_context import current_hosted_telegram_user_id
from aurey.graphs.evm_codec import normalize_evm_address
from aurey.service.state import AureyServiceState
from aurey.telegram.client import resolve_telegram_bot_token

_log = logging.getLogger(__name__)

_proactive_notify_state: AureyServiceState | None = None
_proactive_notify_loop: asyncio.AbstractEventLoop | None = None

_PEER_TRANSFER_EXECUTE_KINDS = frozenset({"native_transfer", "erc20_transfer"})


def _coerce_tool_output(output: Any) -> dict[str, Any] | None:
    """LangChain may pass a dict or a JSON string from tool nodes."""

    if isinstance(output, dict):
        return output
    if isinstance(output, str):
        text = output.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _peer_from_resolve_tool_output(out: dict[str, Any]) -> PeerTransferRecipient | None:
    if not out.get("ok"):
        return None
    res = out.get("result")
    if not isinstance(res, dict):
        return None
    tid = res.get("telegram_user_id")
    eth = res.get("ethereum") or res.get("to_address")
    if tid is None or not eth:
        return None
    try:
        tid_i = int(tid)
        eth_s = normalize_evm_address(str(eth).strip())
    except (TypeError, ValueError):
        return None
    handle = str(res.get("telegram_handle") or f"user:{tid_i}")
    return PeerTransferRecipient(
        telegram_user_id=tid_i,
        telegram_handle=handle,
        evm_address=eth_s,
    )


def _tx_execute_envelope_kind(
    state: AureyServiceState,
    inputs: dict[str, Any],
) -> str | None:
    prepared_id = inputs.get("prepared_id")
    if prepared_id:
        record = state.runtime.prepared_txs.get(str(prepared_id))
        if record is not None and record.kind == "execute_envelope":
            raw = record.payload.get("kind")
            return str(raw).strip() if raw is not None else None
    envelope = inputs.get("envelope")
    if isinstance(envelope, dict):
        raw = envelope.get("kind")
        return str(raw).strip() if raw is not None else None
    return None


def _should_notify_peer_transfer_execute(
    state: AureyServiceState,
    *,
    inputs: dict[str, Any],
    peer_evm_address: str,
) -> bool:
    kind = _tx_execute_envelope_kind(state, inputs)
    if kind not in _PEER_TRANSFER_EXECUTE_KINDS:
        return False
    if kind == "native_transfer":
        to_raw = None
        if inputs.get("prepared_id"):
            record = state.runtime.prepared_txs.get(str(inputs["prepared_id"]))
            if record is not None:
                to_raw = record.payload.get("to")
        envelope = inputs.get("envelope")
        if to_raw is None and isinstance(envelope, dict):
            to_raw = envelope.get("to")
        if to_raw:
            try:
                return normalize_evm_address(str(to_raw)) == normalize_evm_address(peer_evm_address)
            except ValueError:
                return False
    return True


__all__ = [
    "NotifyResult",
    "TransferNotifyCallback",
    "build_invite_recipient_ready_html",
    "build_transfer_received_html",
    "notify_telegram_user",
    "register_proactive_telegram_notify",
    "schedule_invite_sender_recipient_ready_notify",
    "schedule_transfer_received_notify",
]


def register_proactive_telegram_notify(
    state: AureyServiceState,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Called from Telegram bot startup so sync provisioning can schedule DMs."""

    global _proactive_notify_state, _proactive_notify_loop
    _proactive_notify_state = state
    _proactive_notify_loop = loop


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


def build_invite_recipient_ready_html(
    *,
    recipient_display: str,
    target_handle: str,
) -> str:
    who = html.escape(recipient_display, quote=False)
    handle = html.escape((target_handle or "").strip().lstrip("@"), quote=False)
    return (
        f"<b>{who}</b> finished Aurey wallet setup for <b>@{handle}</b>.\n"
        "You can send tokens again using that Telegram handle."
    )


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


def schedule_invite_sender_recipient_ready_notify(
    *,
    sender_telegram_user_id: int,
    recipient_display: str,
    target_handle: str,
) -> None:
    state = _proactive_notify_state
    loop = _proactive_notify_loop
    if state is None or loop is None:
        _log.debug("skip invite sender notify: Telegram proactive notify not registered")
        return

    token = resolve_telegram_bot_token(state)
    html = build_invite_recipient_ready_html(
        recipient_display=recipient_display,
        target_handle=target_handle,
    )

    async def _run() -> None:
        result = await notify_telegram_user(
            bot_token=token,
            telegram_user_id=sender_telegram_user_id,
            html=html,
        )
        if not result.delivered:
            _log.info(
                "invite sender notify not delivered sender=%s handle=@%s detail=%s",
                sender_telegram_user_id,
                target_handle,
                result.detail,
            )

    try:
        asyncio.run_coroutine_threadsafe(_run(), loop)
    except Exception:
        _log.warning("schedule_invite_sender_recipient_ready_notify failed", exc_info=True)


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
        sender_telegram_user_id: int | None = None,
    ) -> None:
        self._state = state
        self._loop = loop
        self._sender_telegram_user_id = sender_telegram_user_id
        self._tool_name_by_run: dict[str, str] = {}
        self._tx_execute_inputs_by_run: dict[str, dict[str, Any]] = {}
        self._pending_peer: PeerTransferRecipient | None = None

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        name = serialized.get("name") if isinstance(serialized, dict) else None
        tool = (name or "").strip()
        self._tool_name_by_run[str(run_id)] = tool
        if tool == "tx_execute":
            raw = kwargs.get("inputs")
            self._tx_execute_inputs_by_run[str(run_id)] = (
                dict(raw) if isinstance(raw, dict) else {}
            )
        return None

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        tool = self._tool_name_by_run.pop(str(run_id), "")
        out = _coerce_tool_output(output)

        if tool == "resolve_hosted_recipient_by_handle" and out is not None:
            peer = _peer_from_resolve_tool_output(out)
            if peer is not None:
                self._pending_peer = peer
                set_peer_transfer_recipient(peer)
            return None

        if tool != "tx_execute":
            return None

        tx_inputs = self._tx_execute_inputs_by_run.pop(str(run_id), {})

        if out is None:
            _log.info(
                "transfer notify skipped reason=tool_output_unparsed type=%s",
                type(output).__name__,
            )
            return None
        if not out.get("ok"):
            _log.info("transfer notify skipped reason=tx_execute_not_ok")
            return None
        result = out.get("result")
        if not isinstance(result, dict):
            _log.info("transfer notify skipped reason=tx_execute_result_not_dict")
            return None
        tx_hash = result.get("tx_hash")
        if isinstance(tx_hash, str):
            tx_hash_out: str | None = tx_hash.strip() or None
        else:
            tx_hash_out = None

        sender_tid = self._sender_telegram_user_id or current_hosted_telegram_user_id.get()
        if sender_tid is None:
            _log.info("transfer notify skipped reason=sender_telegram_context_missing")
            return None

        peer = current_peer_transfer_recipient.get() or self._pending_peer
        notify_source = "resolve_context"
        if peer is None:
            inferred = recipient_evm_from_transfer_execute(self._state, tx_inputs)
            if inferred:
                peer = lookup_peer_recipient_by_wallet(self._state, inferred)
                notify_source = "wallet_db_lookup"
            if peer is None:
                _log.info(
                    "transfer notify skipped reason=no_recipient "
                    "inferred_evm=%s tx_hash=%s",
                    inferred,
                    tx_hash_out,
                )
                return None

        if not (peer.evm_address or "").strip():
            _log.info("transfer notify skipped reason=peer_missing_evm_address")
            return None
        if not _should_notify_peer_transfer_execute(
            self._state,
            inputs=tx_inputs,
            peer_evm_address=peer.evm_address,
        ):
            _log.info(
                "transfer notify skipped reason=not_peer_transfer_execute kind=%s",
                _tx_execute_envelope_kind(self._state, tx_inputs),
            )
            return None

        self._pending_peer = None
        clear_peer_transfer_recipient()

        _log.info(
            "transfer notify scheduled recipient_tid=%s handle=%s source=%s tx_hash=%s",
            peer.telegram_user_id,
            peer.telegram_handle,
            notify_source,
            tx_hash_out,
        )
        schedule_transfer_received_notify(
            self._state,
            loop=self._loop,
            sender_telegram_user_id=int(sender_tid),
            recipient_telegram_user_id=peer.telegram_user_id,
            recipient_handle=peer.telegram_handle,
            tx_hash=tx_hash_out,
        )
        return None
