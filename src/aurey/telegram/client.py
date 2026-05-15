"""Optional Telegram client that reuses the shared Aurey invoke service path."""

from __future__ import annotations

import asyncio
import html
import logging
import queue
import re
from collections.abc import Callable
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from aurey.custody.errors import SecretNotFoundError, SecretStoreUnavailableError
from aurey.principal_augment import augment_runtime_for_principal
from aurey.service.bootstrap import bootstrap_aurey_service_state
from aurey.service.invoke import AgentInvokeResult, invoke_deep_agent_turn
from aurey.service.message_content import reply_preview_from_summary
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings


class TelegramConfigurationError(RuntimeError):
    """Telegram setup failed without exposing token paths or values."""


def _telegram_chat_is_allowed(chat_id: int | None, allowed: frozenset[int] | None) -> bool:
    """When ``allowed`` is set, only listed chats may invoke the bot."""

    if allowed is None:
        return True
    if chat_id is None:
        return False
    return chat_id in allowed


_TELEGRAM_MAX_MESSAGE_CHARS = 4096
_TELEGRAM_CHUNK_TARGET_CHARS = 3600
_TELEGRAM_TYPING_REFRESH_SEC = 4.0
_TELEGRAM_STATUS_EDIT_THROTTLE_SEC = 1.25
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEX_INLINE_BACKTICK_RE = re.compile(r"`(0x(?:[a-fA-F0-9]{64}|[a-fA-F0-9]{40}))`")
_SKIP_ANCHORS_AND_CODE_RE = re.compile(
    r"(<code>[\s\S]*?</code>|<a\b[^>]*>[\s\S]*?</a>)"
)
_TX_HASH_RE = re.compile(r"\b(0x[a-fA-F0-9]{64})\b")
_ADDRESS_RE = re.compile(r"\b(0x[a-fA-F0-9]{40})\b")

_CHAIN_EXPLORER_BY_ID: dict[int, str] = {
    1: "https://etherscan.io",
    8453: "https://basescan.org",
    42161: "https://arbiscan.io",
    10: "https://optimistic.etherscan.io",
    137: "https://polygonscan.com",
    56: "https://bscscan.com",
    59144: "https://lineascan.build",
    534352: "https://scrollscan.com",
    324: "https://explorer.zksync.io",
    43114: "https://snowtrace.io",
}

_CHAIN_ID_HINT_RE = re.compile(r"(?i)\bchain(?:\s+i?d|\s+#)?\s*(?:[:=]|is)\s*(?:(?:#|=\s*|id\s+)\s*)?(\d+)\b")
_STANDALONE_CHAIN_ID_RE = re.compile(r"\b(8453|42161|59144|534352|43114)\b")


def _explicit_explorer_base_for_line(line: str) -> str | None:
    """Return explorer URL when ``line`` signals a chain; ``None`` to inherit sticky paragraph context."""

    m = _CHAIN_ID_HINT_RE.search(line)
    if m is not None:
        cid = int(m.group(1))
        if cid in _CHAIN_EXPLORER_BY_ID:
            return _CHAIN_EXPLORER_BY_ID[cid]
    ms = _STANDALONE_CHAIN_ID_RE.search(line)
    if ms is not None:
        return _CHAIN_EXPLORER_BY_ID[int(ms.group(1))]
    keyword_rules: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"(?i)\b(?:base(?:\s+mainnet|\s+l2|\s+l2:?)?|\bon\s+base\b)(?!\s*i/o)"), _CHAIN_EXPLORER_BY_ID[8453]),
        (re.compile(r"(?i)\b(?:usdc\s+on\s+base\b)"), _CHAIN_EXPLORER_BY_ID[8453]),
        (re.compile(r"(?i)\b(?:arbitrum|arb\s+(?:mainnet|one))\b|\b42161\b"), _CHAIN_EXPLORER_BY_ID[42161]),
        (re.compile(r"(?i)\b(?:optimism|op\s+(?:chain|stack|mainnet))\b"), _CHAIN_EXPLORER_BY_ID[10]),
        (re.compile(r"(?i)\bpolygon\b|\b(?:matic\b)\s+(?:network|polygon)"), _CHAIN_EXPLORER_BY_ID[137]),
        (re.compile(r"(?i)\b(?:bnb|bsc)\s+(?:smart\s+)?chain\b|\b(?:binance|bnb)\s+chain\b|\bbsc\b"), _CHAIN_EXPLORER_BY_ID[56]),
        (re.compile(r"(?i)\blinea\b"), _CHAIN_EXPLORER_BY_ID[59144]),
        (re.compile(r"(?i)\bscroll\b(?:\s+mainnet|\s+L2\b)?"), _CHAIN_EXPLORER_BY_ID[534352]),
        (re.compile(r"(?i)\b(?:zk\s*s?ync|zkSync)\s+era\b"), _CHAIN_EXPLORER_BY_ID[324]),
        (re.compile(r"(?i)\b(?:avalanche|avax)\b"), _CHAIN_EXPLORER_BY_ID[43114]),
        (
            re.compile(
                r"(?i)\(\s*ethereum\s*\)|\b(?:ethereum\b|\beth(?:ereum)?(?:\s+mainnet|:|\))"
                r"|\bweth\b\s*\(\s*ethereum\s*\)|(?:^|[\s(])eth(?:ereum)?(?:\)\s*[→:]|\s*mainnet))"
            ),
            _CHAIN_EXPLORER_BY_ID[1],
        ),
    ]
    for pat, base_url in keyword_rules:
        if pat.search(line):
            return base_url
    return None


def _link_evm_explorer_entities(html_fragment: str, *, explorer_base: str) -> str:
    """Wrap tx hashes and contracts in Telegram-safe ``<a>`` URLs; ``html_fragment`` is already escaped."""

    def _subs(segment: str) -> str:
        def tx_repl(m: re.Match[str]) -> str:
            h = m.group(1)
            return f'<a href="{explorer_base}/tx/{h}">{h}</a>'

        segment = _TX_HASH_RE.sub(tx_repl, segment)

        def addr_repl(m: re.Match[str]) -> str:
            a = m.group(1)
            return f'<a href="{explorer_base}/address/{a}">{a}</a>'

        return _ADDRESS_RE.sub(addr_repl, segment)

    pieces = _SKIP_ANCHORS_AND_CODE_RE.split(html_fragment)
    for i in range(0, len(pieces), 2):
        pieces[i] = _subs(pieces[i])
    return "".join(pieces)


def _format_inline_markdown(text: str, *, explorer_base: str) -> str:
    """Small Markdown subset to Telegram HTML, after escaping user/model text."""

    escaped = html.escape(text, quote=False)

    def _hex_backtick_repl(m: re.Match[str]) -> str:
        inner = m.group(1)
        if len(inner) == 66:
            return f'<a href="{explorer_base}/tx/{inner}">{inner}</a>'
        return f'<a href="{explorer_base}/address/{inner}">{inner}</a>'

    escaped = _HEX_INLINE_BACKTICK_RE.sub(_hex_backtick_repl, escaped)
    escaped = _INLINE_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", escaped)
    escaped = _link_evm_explorer_entities(escaped, explorer_base=explorer_base)
    return escaped


def format_telegram_message(text: str) -> str:
    """Render common agent Markdown as Telegram-safe HTML.

    The model speaks mostly Markdown; Telegram's HTML parse mode is stricter but safer
    than MarkdownV2 because we escape first and only then add a small allowed tag set.
    """

    out: list[str] = []
    in_code = False
    code_lines: list[str] = []
    sticky_explorer = _CHAIN_EXPLORER_BY_ID[1]

    def _update_sticky(fragment: str) -> None:
        nonlocal sticky_explorer
        cue = _explicit_explorer_base_for_line(fragment)
        if cue is not None:
            sticky_explorer = cue

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                out.append(f"<pre>{html.escape(chr(10).join(code_lines), quote=False)}</pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        stripped = line.strip()
        if stripped.startswith("### "):
            body = stripped[4:]
            _update_sticky(body)
            out.append(f"<b>{_format_inline_markdown(body, explorer_base=sticky_explorer)}</b>")
        elif stripped.startswith("## "):
            body = stripped[3:]
            _update_sticky(body)
            out.append(f"<b>{_format_inline_markdown(body, explorer_base=sticky_explorer)}</b>")
        elif stripped.startswith("# "):
            body = stripped[2:]
            _update_sticky(body)
            out.append(f"<b>{_format_inline_markdown(body, explorer_base=sticky_explorer)}</b>")
        else:
            _update_sticky(line)
            out.append(_format_inline_markdown(line, explorer_base=sticky_explorer))

    if in_code:
        out.append(f"<pre>{html.escape(chr(10).join(code_lines), quote=False)}</pre>")

    return "\n".join(out).strip() or "Done."


def telegram_message_chunks(text: str) -> list[str]:
    """Split raw text before HTML formatting so tags are never cut in half."""

    if len(text) <= _TELEGRAM_MAX_MESSAGE_CHARS:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in text.split("\n\n"):
        part_len = len(paragraph) + (2 if current else 0)
        if current and current_len + part_len > _TELEGRAM_CHUNK_TARGET_CHARS:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0

        if len(paragraph) > _TELEGRAM_CHUNK_TARGET_CHARS:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            for i in range(0, len(paragraph), _TELEGRAM_CHUNK_TARGET_CHARS):
                chunks.append(paragraph[i : i + _TELEGRAM_CHUNK_TARGET_CHARS])
            continue

        current.append(paragraph)
        current_len += part_len

    if current:
        chunks.append("\n\n".join(current))
    return chunks or ["Done."]


def resolve_telegram_bot_token(state: AureyServiceState) -> str:
    """Resolve the Telegram bot token via SecretStore using a configured vault path."""

    path = state.settings.telegram_bot_token_secret_path
    if not path:
        raise TelegramConfigurationError("Telegram bot token secret path is not configured.")
    try:
        return state.runtime.secret_store.get_secret(path).reveal()
    except SecretNotFoundError as exc:
        raise TelegramConfigurationError("Telegram bot token could not be resolved.") from exc
    except SecretStoreUnavailableError as exc:
        raise TelegramConfigurationError(
            f"Secret store unavailable for Telegram token at path {path!r}. ({exc})"
        ) from exc


def resolve_telegram_start_reply(
    state: AureyServiceState,
    *,
    telegram_user_id: int,
    display_name: str | None = None,
) -> str:
    """Return the ``/start`` reply text when cloud onboarding might bypass the agent."""

    if state.onboarding is None:
        return "Aurey is ready. Send a message to invoke the agent."
    outcome = state.onboarding.run_telegram_start(
        telegram_user_id=int(telegram_user_id),
        display_name=display_name,
    )
    return outcome.message


def _telegram_status_progress_html(label: str) -> str:
    line = label.strip() or "…"
    return f"<i>{html.escape(line)}</i>"


class TelegramInvokeProgressCallback(BaseCallbackHandler):
    """Feeds short, vague status lines while the LangGraph agent runs (Telegram UI)."""

    def __init__(self, sink: Callable[[str], None]) -> None:
        super().__init__()
        self._sink = sink

    @staticmethod
    def _meta(kwargs: dict[str, Any]) -> dict[str, Any]:
        m = kwargs.get("metadata")
        return m if isinstance(m, dict) else {}

    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: list[list[Any]],
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        meta = self._meta(kwargs)
        if meta.get("langgraph_node") == "model":
            self._sink("Thinking…")

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        _ = serialized, input_str, run_id, kwargs
        self._sink("Gathering details…")


def _last_text_message(result: AgentInvokeResult) -> str:
    text = reply_preview_from_summary(result.messages)
    return text if text else "Done."


def handle_telegram_text(
    state: AureyServiceState,
    *,
    chat_id: int | str,
    text: str,
    user_id: int | str | None = None,
    model: str | None = None,
    progress_sink: Callable[[str], None] | None = None,
) -> str:
    """Handle one inbound Telegram text message and return safe text for ``reply_text``."""

    if state.onboarding is not None and user_id is not None:
        try:
            tg_uid = int(user_id)
        except (TypeError, ValueError):
            tg_uid = None
        if tg_uid is not None:
            gate = state.onboarding.blocking_agent_message_for_telegram_user(tg_uid)
            if gate:
                return gate

    session_id = f"telegram:{chat_id}"
    context: dict[str, Any] = {"telegram_chat_id": str(chat_id)}
    if user_id is not None:
        context["telegram_user_id"] = str(user_id)

    graph_suffix = ""
    overlay = None
    hosted_wallet: str | None = None
    if state.onboarding is not None and user_id is not None:
        try:
            tg_uid = int(user_id)
        except (TypeError, ValueError):
            tg_uid = None
        if tg_uid is not None:
            principal = state.onboarding.user_principal_for_telegram_user(tg_uid)
            if principal is not None:
                session_id = f"user:{principal.db_user_id}"
                graph_suffix = f"cloud:{principal.db_user_id}"
                context["platform_user_id"] = principal.db_user_id
                context["oneclaw_user_agent_id"] = principal.user_agent_id
                hosted_wallet = principal.wallet_address
                if state.settings.evm_signing_mode == "oneclaw_intents":
                    if not state.settings.cloud_hosted_intents_signing_enabled:
                        return (
                            "On-chain actions for your linked wallet need hosted Intents signing, "
                            "which is turned off for this server. The operator should set "
                            "AUREY_CLOUD_HOSTED_INTENTS_SIGNING_ENABLED=true only after POST "
                            "/v1/auth/delegated-token is available on 1Claw and grant JWTs are "
                            "stored in the operator vault (see runbook). Until then, use "
                            "AUREY_EVM_SIGNING_MODE=vault_key for the operator wallet, or finish "
                            "wiring hosted signing."
                        )
                    try:
                        overlay = augment_runtime_for_principal(state.runtime, principal)
                    except RuntimeError as exc:
                        log = logging.getLogger("aurey.telegram")
                        log.warning("Hosted user turn could not build signing runtime: %s", exc)
                        return (
                            "I cannot sign transactions for your linked account yet "
                            f"(setup: {exc}). Ask the operator to check hosted grant paths and "
                            "1Claw delegated-token availability."
                        )

    extras = [TelegramInvokeProgressCallback(progress_sink)] if progress_sink is not None else None
    result = invoke_deep_agent_turn(
        state,
        message=text,
        session_id=session_id,
        context=context,
        model=model,
        extra_callbacks=extras,
        runtime_overlay=overlay,
        graph_cache_suffix=graph_suffix,
        hosted_wallet_address=hosted_wallet,
    )
    if result.ok:
        return _last_text_message(result)
    assert result.error is not None
    return f"Aurey error ({result.error.code}): {result.error.message}"


def _import_telegram_ext():
    try:
        from telegram import Update
        from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Telegram support requires the optional 'telegram' extra: "
            "pip install -e '.[telegram]'"
        ) from exc
    return Application, CommandHandler, ContextTypes, MessageHandler, Update, filters


def build_telegram_application(
    *,
    state: AureyServiceState,
    token: str | None = None,
    model: str | None = None,
):
    """Build a python-telegram-bot Application with Aurey's shared invoke path."""

    (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        Update,
        filters,
    ) = _import_telegram_ext()
    bot_token = token or resolve_telegram_bot_token(state)
    allowed_chats = state.settings.telegram_allowed_chat_id_allowlist
    gate_log = logging.getLogger("aurey.telegram.bot")

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        _ = context
        chat = update.effective_chat
        chat_id_raw = getattr(chat, "id", None)
        cid_opt = int(chat_id_raw) if chat_id_raw is not None else None
        if not _telegram_chat_is_allowed(cid_opt, allowed_chats):
            gate_log.debug("Telegram /start ignored (disallowed chat_id=%r)", chat_id_raw)
            return
        if update.effective_message is None:
            return
        user = update.effective_user
        if user is None:
            await update.effective_message.reply_text("Could not resolve the Telegram user.")
            return

        parts: list[str] = []
        if getattr(user, "username", None):
            parts.append("@" + str(user.username))
        full_name = getattr(user, "full_name", None)
        if full_name:
            parts.append(str(full_name))
        display_name = " ".join(parts).strip() or None

        body = resolve_telegram_start_reply(
            state,
            telegram_user_id=int(user.id),
            display_name=display_name,
        )
        await update.effective_message.reply_text(body)

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if msg is None or not msg.text:
            return
        chat = update.effective_chat
        user = update.effective_user
        chat_id_raw = getattr(chat, "id", None)
        cid_opt = int(chat_id_raw) if chat_id_raw is not None else None
        if not _telegram_chat_is_allowed(cid_opt, allowed_chats):
            gate_log.debug("Telegram message ignored (disallowed chat_id=%r)", chat_id_raw)
            return
        chat_id_for_session = chat_id_raw if chat_id_raw is not None else "unknown"

        if chat_id_raw is None:
            reply = await asyncio.to_thread(
                handle_telegram_text,
                state,
                chat_id=chat_id_for_session,
                user_id=getattr(user, "id", None),
                text=msg.text,
                model=model,
            )
            for chunk in telegram_message_chunks(reply):
                await msg.reply_text(
                    format_telegram_message(chunk),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            return

        chat_id_int = int(chat_id_raw)
        from telegram.constants import ChatAction
        from telegram.error import BadRequest

        typing_done = asyncio.Event()

        async def pump_typing() -> None:
            """Refresh ``typing``; Telegram clears it after a few seconds."""
            while not typing_done.is_set():
                try:
                    await context.bot.send_chat_action(chat_id=chat_id_int, action=ChatAction.TYPING)
                except Exception:
                    pass
                if typing_done.is_set():
                    break
                try:
                    await asyncio.wait_for(typing_done.wait(), timeout=_TELEGRAM_TYPING_REFRESH_SEC)
                except TimeoutError:
                    pass

        typing_task = asyncio.create_task(pump_typing())

        reply = ""
        try:
            progress_q: queue.SimpleQueue[str] = queue.SimpleQueue()

            status_msg = await msg.reply_text(
                _telegram_status_progress_html("Getting ready…"),
                parse_mode="HTML",
            )

            invoke_task = asyncio.create_task(
                asyncio.to_thread(
                    handle_telegram_text,
                    state,
                    chat_id=chat_id_for_session,
                    user_id=getattr(user, "id", None),
                    text=msg.text,
                    model=model,
                    progress_sink=progress_q.put_nowait,
                )
            )

            applied_label = ""
            next_edit_at = 0.0
            latest_line: str | None = None

            async def flush_progress(*, force: bool) -> None:
                nonlocal applied_label, next_edit_at, latest_line
                if latest_line is None or latest_line == applied_label:
                    return
                now = asyncio.get_running_loop().time()
                if not force and not invoke_task.done() and now < next_edit_at:
                    return
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id_int,
                        message_id=status_msg.message_id,
                        text=_telegram_status_progress_html(latest_line),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    applied_label = latest_line
                    next_edit_at = now + _TELEGRAM_STATUS_EDIT_THROTTLE_SEC
                except BadRequest:
                    pass

            while not invoke_task.done():
                await asyncio.sleep(0.35)
                try:
                    while True:
                        latest_line = progress_q.get_nowait()
                except queue.Empty:
                    pass
                await flush_progress(force=False)

            reply = await invoke_task
            try:
                while True:
                    latest_line = progress_q.get_nowait()
            except queue.Empty:
                pass
            await flush_progress(force=True)
        finally:
            typing_done.set()
            await typing_task

        chunks = telegram_message_chunks(reply)
        for idx, raw_chunk in enumerate(chunks):
            body = format_telegram_message(raw_chunk)
            if idx == 0:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id_int,
                        message_id=status_msg.message_id,
                        text=body,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    continue
                except BadRequest:
                    pass
            await msg.reply_text(
                body,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

    app = Application.builder().token(bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    _telegram_log = logging.getLogger("aurey.telegram.bot")
    from telegram.error import Conflict as TelegramConflict

    async def _telegram_error_handler(
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        err = context.error
        if isinstance(err, TelegramConflict):
            _telegram_log.error(
                "Telegram Conflict while handling an update — another client may be "
                "calling getUpdates with the same bot token. Stop duplicate pollers."
            )
            return
        _telegram_log.error("Telegram handler raised", exc_info=err)

    app.add_error_handler(_telegram_error_handler)
    return app


def create_telegram_application(
    *,
    state: AureyServiceState | None = None,
    settings: AureySettings | None = None,
    model: str | None = None,
):
    """Bootstrap service state and return a Telegram polling Application."""

    svc = state or bootstrap_aurey_service_state(settings)
    return build_telegram_application(state=svc, model=model)


__all__ = [
    "TelegramConfigurationError",
    "build_telegram_application",
    "create_telegram_application",
    "format_telegram_message",
    "handle_telegram_text",
    "resolve_telegram_bot_token",
    "resolve_telegram_start_reply",
    "telegram_message_chunks",
]
