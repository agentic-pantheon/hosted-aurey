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

from aurey.cloud.signing_context import HostedSigningContext
from aurey.custody.errors import SecretNotFoundError, SecretStoreUnavailableError
from aurey.graphs.evm_codec import to_checksum_evm_address
from aurey.service.bootstrap import bootstrap_aurey_service_state
from aurey.service.invoke import AgentInvokeResult, invoke_deep_agent_turn
from aurey.service.message_content import reply_preview_from_summary
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings


class TelegramConfigurationError(RuntimeError):
    """Telegram setup failed without exposing token paths or values."""


def _telegram_current_allowed_chat_ids(state: AureyServiceState) -> frozenset[int] | None:
    """Read allowlist from settings on each update (ops may change env without rebuilding the app)."""

    return state.settings.telegram_allowed_chat_id_allowlist


def _telegram_chat_is_allowed(
    chat_id: int | None,
    allowed: frozenset[int] | None,
    *,
    telegram_user_id: int | None = None,
) -> bool:
    """When ``allowed`` is set, only listed chats may invoke the bot.

    Private DM chat ids equal the user's Telegram id; operators sometimes add one or
    the other to ``AUREY_TELEGRAM_ALLOWED_CHAT_IDS``, so either id matching the set
    is treated as allowed.
    """

    if allowed is None:
        return True
    if chat_id is not None and chat_id in allowed:
        return True
    if telegram_user_id is not None and telegram_user_id in allowed:
        return True
    return False


def _telegram_chat_allowlist_enforced(allowed: frozenset[int] | None) -> bool:
    return allowed is not None


async def _telegram_clear_access_request_after_allowlist(
    state: AureyServiceState,
    *,
    telegram_user_id: int,
    user_data: dict[str, object],
) -> None:
    """Drop beta access-request state once the user is on the allowlist."""

    from aurey.cloud.hosted_access import (
        clear_telegram_access_request_flow,
        delete_telegram_access_request,
    )

    clear_telegram_access_request_flow(user_data)
    factory = state.hosted_session_factory
    if factory is None:
        return

    def _delete_sync() -> None:
        db = factory()
        try:
            delete_telegram_access_request(db, telegram_user_id=telegram_user_id)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    await asyncio.to_thread(_delete_sync)


async def _telegram_handle_disallowed_chat_access_request(
    update: object,
    context: object,
    *,
    state: AureyServiceState,
    message_text: str | None,
) -> bool:
    """Run beta access-request flow when chat is outside ``AUREY_TELEGRAM_ALLOWED_CHAT_IDS``.

    Returns ``True`` when the update was handled (caller should return).
    """

    allowed_chats = _telegram_current_allowed_chat_ids(state)
    if not _telegram_chat_allowlist_enforced(allowed_chats):
        return False

    from telegram import Update

    if not isinstance(update, Update):
        return False

    chat = update.effective_chat
    chat_id_raw = getattr(chat, "id", None)
    cid_opt = int(chat_id_raw) if chat_id_raw is not None else None
    msg = update.effective_message
    user = update.effective_user
    tid_raw = getattr(user, "id", None)
    tid_opt = int(tid_raw) if tid_raw is not None else None
    if _telegram_chat_is_allowed(
        cid_opt,
        allowed_chats,
        telegram_user_id=tid_opt,
    ):
        return False

    if msg is None or tid_raw is None:
        return True

    user_data = getattr(context, "user_data", {}) or {}

    from aurey.cloud.hosted_access import telegram_access_request_flow_step

    reply = await asyncio.to_thread(
        telegram_access_request_flow_step,
        state,
        telegram_user_id=int(tid_raw),
        telegram_username=getattr(user, "username", None),
        telegram_chat_id=cid_opt,
        message_text=message_text,
        user_data=user_data,
    )
    await msg.reply_text(reply)
    return True


_HOSTED_AGENT_BLOCK_EMAIL_STATES = frozenset(
    {
        "awaiting_email",
        "awaiting_email_verification",
    }
)

_HOSTED_CHAT_EMAIL_REPLY_STATES = frozenset(
    {
        "awaiting_email",
        "awaiting_email_verification",
    }
)

_HOSTED_EMAIL_CANCEL_STATES = frozenset(
    {
        "awaiting_email",
        "awaiting_email_verification",
        "email_verified",
    }
)


def _hosted_finish_claim_short_message() -> str:
    return "Finish hosted setup first: open the claim link from /start, then message me again."


def _hosted_finish_claim_via_email_short_message(*, verified_email_required: bool) -> str:
    if verified_email_required:
        return (
            "Finish hosted setup first — check your email (and spam/junk) for the latest claim "
            "link, open it once (set your password there), then message me again."
        )
    return _hosted_finish_claim_short_message()


def _hosted_inbox_verified(row: object) -> bool:
    """True when the hosted row has a verified real email (not claim completion)."""

    from aurey.cloud.models import HostedPlatformUserORM

    if not isinstance(row, HostedPlatformUserORM):
        return False
    if row.email_verified_at is None:
        return False
    return bool((row.email or "").strip())


def _hosted_blocked_email_prompt_message(onboarding_state: str) -> str:
    """Short instruction when onboarding has not reached claim yet."""

    st = (onboarding_state or "").strip()
    if st == "awaiting_email":
        return (
            "Reply with your real email address to receive a verification code "
            "(one email per message). Check spam/junk if the code does not arrive."
        )
    if st == "awaiting_email_verification":
        return (
            "Reply with the 6-digit code we emailed you. If you do not see it, check spam/junk."
        )
    if st == "email_verified":
        return (
            "Provisioning — tap /start in a moment to receive your claim link by email "
            "(check spam/junk if needed)."
        )
    return "Tap /start to continue Aurey setup."


def _hosted_snapshot_row_onboarding_claim(
    state: AureyServiceState,
    *,
    telegram_user_id: int,
) -> tuple[str | None, str | None]:
    """Return `(onboarding_state, claim_url)` from DB without mutating."""

    cfg = state.settings
    if not cfg.hosted_platform_enabled or not (cfg.database_url or "").strip():
        return None, None
    factory = state.hosted_session_factory
    if factory is None:
        return None, None

    from sqlalchemy import select

    from aurey.cloud.models import HostedPlatformUserORM

    db = factory()
    try:
        row = db.scalar(
            select(HostedPlatformUserORM).where(
                HostedPlatformUserORM.telegram_user_id == telegram_user_id,
            ),
        )
        if row is None:
            return None, None
        onboard = (row.onboarding_state or "").strip()
        cu = (row.claim_url or "").strip()
        return onboard, cu or None
    finally:
        db.close()


async def hosted_handle_optional_email_onboarding_chat(
    *,
    state: AureyServiceState,
    telegram_user_id: int,
    telegram_username: str | None,
    text: str,
) -> str | None:
    """Handle email OTP chat steps; returns Telegram reply HTML text or ``None``."""

    cfg = state.settings
    db_url = (cfg.database_url or "").strip()
    if not cfg.hosted_platform_enabled or not db_url or not cfg.hosted_require_verified_email:
        return None
    factory = state.hosted_session_factory
    if factory is None:
        return None

    trimmed_raw = text.strip()
    trimmed_lower = trimmed_raw.lower()
    wants_cancel = trimmed_lower.startswith("/cancel")
    onboarding_before, _cu0 = _hosted_snapshot_row_onboarding_claim(
        state, telegram_user_id=telegram_user_id
    )

    if (
        onboarding_before or ""
    ).strip() not in _HOSTED_CHAT_EMAIL_REPLY_STATES and not wants_cancel:
        return None

    from aurey.cloud.hosted_verification import (
        HostedVerificationError,
        confirm_email_verification,
        purge_pending_hosted_verifications,
        start_email_verification,
    )
    from aurey.cloud.onboarding_refresh import refresh_hosted_user_claim_state
    from aurey.cloud.platform_client import HostedPlatformApiError, OneClawPlatformClient
    from aurey.cloud.provision import (
        HostedProvisioningError,
        ensure_hosted_telegram_row,
        ensure_telegram_user_provisioned,
    )

    def _txn() -> str:
        plat = OneClawPlatformClient.from_settings(cfg)
        vault_http = getattr(state.runtime, "oneclaw_evm_signer", None)
        from aurey.custody.secret_store import OneClawHttpClient

        vf = vault_http if isinstance(vault_http, OneClawHttpClient) else None

        factory_inner = state.hosted_session_factory
        if factory_inner is None:
            raise RuntimeError("hosted_session_factory is not configured.")
        db = factory_inner()
        try:
            if wants_cancel:
                cancel_st = (onboarding_before or "").strip()
                if cancel_st not in _HOSTED_EMAIL_CANCEL_STATES:
                    db.rollback()
                    return (
                        "Nothing to cancel — you are not in email verification. "
                        "Use /start for claim help."
                    )
                row_cancel = ensure_hosted_telegram_row(
                    db,
                    cfg,
                    telegram_user_id=telegram_user_id,
                    username=telegram_username,
                )
                if row_cancel is None:
                    db.rollback()
                    return "Tap /start to begin setup."
                purge_pending_hosted_verifications(db, row_cancel.id)
                row_cancel.onboarding_state = "awaiting_email"
                row_cancel.email = None
                row_cancel.email_verified_at = None
                db.commit()
                return "Canceled — send a new email address or tap /start."

            base_row = ensure_hosted_telegram_row(
                db,
                cfg,
                telegram_user_id=telegram_user_id,
                username=telegram_username,
            )
            if base_row is None:
                db.rollback()
                return ""

            onboard = (base_row.onboarding_state or "").strip()

            if onboard == "awaiting_email":
                if trimmed_lower.startswith("/"):
                    db.commit()
                    return (
                        "<b>Aurey</b>\n"
                        "Send your <b>real email</b> in one message (not a command). "
                        "Or tap /start above."
                    )
                try:
                    start_email_verification(db, cfg, base_row, trimmed_raw)
                except HostedVerificationError as exc:
                    db.rollback()
                    return html.escape(str(exc), quote=False)
                db.commit()
                return (
                    "<b>Verification sent</b>\n"
                    "Open your inbox for a 6-digit code (check spam/junk if it is missing), "
                    "then reply here with digits only."
                )

            if onboard == "awaiting_email_verification":
                try:
                    confirm_email_verification(db, cfg, base_row, trimmed_raw)
                except HostedVerificationError as exc:
                    db.rollback()
                    return html.escape(str(exc), quote=False)
                try:
                    row2, _ = ensure_telegram_user_provisioned(
                        db,
                        cfg,
                        plat,
                        telegram_user_id=telegram_user_id,
                        username=telegram_username,
                        vault_http_client=vf,
                    )
                    try:
                        refresh_hosted_user_claim_state(db, cfg, plat, row2)
                    except HostedPlatformApiError:
                        pass
                except HostedProvisioningError as exc:
                    db.rollback()
                    return html.escape(
                        f"Provisioning failed (configuration): {exc}",
                        quote=False,
                    )
                db.commit()
                email_hint = ""
                mail = (row2.email or "").strip()
                if mail and "@" in mail:
                    local, _, domain = mail.partition("@")
                    mask = "***" if len(local) <= 1 else local[0] + "***"
                    email_hint = f"Check <b>{mask}@{domain}</b> "
                curl = (row2.claim_url or "").strip()
                if not curl:
                    return (
                        "<b>Provisioning…</b> Your claim invite will arrive by email when ready "
                        "(check spam/junk). Use /start if nothing arrives after a minute."
                    )
                return (
                    "<b>Almost done</b>\n"
                    f"{email_hint}for your <b>claim link</b> (check spam/junk if you do not "
                    "see it).\n\n"
                    "<b>Claiming</b> means taking ownership of your <b>agent credentials</b> "
                    "and the <b>Aurey wallet</b> "
                    "agent. You can do that whenever you like — and you can chat with me now even "
                    "before you claim.\n\n"
                    "If the link expires or you need a new one later, send <b>/start</b> and we will "
                    "email a fresh claim invite."
                )

            db.rollback()
            return ""
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    out = await asyncio.to_thread(_txn)
    if not out.strip():
        return None
    return out


def _hosted_user_must_finish_claim_message(
    state: AureyServiceState,
    *,
    telegram_user_id: int,
) -> str | None:
    """Return reply text when onboarding blocks normal agent invokes.

    ``None`` means normal agent invoke should proceed.
    """

    cfg = state.settings
    if not cfg.hosted_platform_enabled:
        return None
    if not (cfg.database_url or "").strip():
        return None
    factory = state.hosted_session_factory
    if factory is None:
        return None

    from sqlalchemy import select

    from aurey.cloud.models import HostedPlatformUserORM
    from aurey.cloud.onboarding_refresh import refresh_hosted_user_claim_state
    from aurey.cloud.platform_client import HostedPlatformApiError, OneClawPlatformClient

    db = factory()
    try:
        row_preview = db.scalar(
            select(HostedPlatformUserORM).where(
                HostedPlatformUserORM.telegram_user_id == telegram_user_id,
            ),
        )
        onboard_pre = (
            (row_preview.onboarding_state or "").strip() if row_preview is not None else None
        )

        if row_preview is None and cfg.hosted_require_verified_email:
            db.rollback()
            return "Tap /start in this chat to begin Aurey onboarding."

        if (
            onboard_pre is not None
            and onboard_pre in _HOSTED_AGENT_BLOCK_EMAIL_STATES
            and cfg.hosted_require_verified_email
        ):
            db.commit()
            return _hosted_blocked_email_prompt_message(onboard_pre)

        platform = OneClawPlatformClient.from_settings(cfg)
        row = refresh_hosted_user_claim_state(db, cfg, platform, telegram_user_id)
        db.commit()
    except HostedPlatformApiError:
        db.rollback()
        return (
            "Could not verify hosted setup yet—try again in a moment, or use /start for your "
            "claim link."
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if row is None:
        return None
    if (row.onboarding_state or "").strip() == "awaiting_claim":
        if _hosted_inbox_verified(row):
            return None
        if cfg.hosted_require_verified_email:
            return (
                "Verify your email first — reply with your inbox, then the 6-digit code we send."
            )
        return _hosted_finish_claim_short_message()
    return None


_TELEGRAM_MAX_MESSAGE_CHARS = 4096
_TELEGRAM_CHUNK_TARGET_CHARS = 3600
_TELEGRAM_TYPING_REFRESH_SEC = 4.0


def _telegram_begin_typing_pump(
    bot: Any,
    chat_id: int,
) -> tuple[asyncio.Event, asyncio.Task[None]]:
    """Start a background task that keeps Telegram ``typing`` active until ended."""

    from telegram.constants import ChatAction

    typing_done = asyncio.Event()

    async def pump() -> None:
        while not typing_done.is_set():
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:
                pass
            if typing_done.is_set():
                break
            try:
                await asyncio.wait_for(typing_done.wait(), timeout=_TELEGRAM_TYPING_REFRESH_SEC)
            except TimeoutError:
                pass

    return typing_done, asyncio.create_task(pump())


async def _telegram_end_typing_pump(
    typing_done: asyncio.Event,
    typing_task: asyncio.Task[None],
) -> None:
    typing_done.set()
    await typing_task


def _telegram_bot_command_menu(
    *,
    hosted_email_onboarding: bool,
    miniapp_portfolio: bool = False,
) -> list[Any]:
    """Menu entries for Telegram ``setMyCommands`` (BotCommand objects)."""

    from telegram import BotCommand

    cmds = [
        BotCommand("start", "Set up or refresh your Aurey / Claim credentials and wallet"),
        BotCommand("help", "Show available commands"),
    ]
    if miniapp_portfolio:
        cmds.append(BotCommand("portfolio", "Open portfolio Web App"))
    if hosted_email_onboarding:
        cmds.append(
            BotCommand("cancel", "Cancel in-progress email verification and start over"),
        )
    return cmds


def _telegram_help_message_html(
    *,
    hosted_email_onboarding: bool,
    miniapp_portfolio: bool = False,
) -> str:
    lines = [
        "<b>Aurey commands</b>",
        "",
        "<b>/start</b> — Begin setup, check onboarding status, or request a "
        "fresh credentials and wallet claim link by email (if yours expired).",
        "<b>/help</b> — Show this list.",
    ]
    if miniapp_portfolio:
        lines.extend(
            ["<b>/portfolio</b> — Open the visual portfolio Web App (balances, DeFi)."],
        )
    if hosted_email_onboarding:
        lines.extend(
            [
                "<b>/cancel</b> — Abort the current email verification step and enter a new "
                "address.",
                "",
                "During setup, send your <b>real email</b> and then the <b>6-digit code</b> "
                "as normal messages (not commands).",
            ]
        )
    lines.extend(
        [
            "",
            "Any other text invokes the Aurey agent (balances, swaps, Earn, and more) once "
            "your inbox is verified.",
        ]
    )
    return "\n".join(lines)


_TELEGRAM_STATUS_EDIT_THROTTLE_SEC = 1.25
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEX_INLINE_BACKTICK_RE = re.compile(r"`(0x(?:[a-fA-F0-9]{64}|[a-fA-F0-9]{40}))`")
_SKIP_ANCHORS_AND_CODE_RE = re.compile(r"(<code>[\s\S]*?</code>|<a\b[^>]*>[\s\S]*?</a>)")
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

_CHAIN_ID_HINT_RE = re.compile(
    r"(?i)\bchain(?:\s+i?d|\s+#)?\s*(?:[:=]|is)\s*(?:(?:#|=\s*|id\s+)\s*)?(\d+)\b"
)
_STANDALONE_CHAIN_ID_RE = re.compile(r"\b(8453|42161|59144|534352|43114)\b")


def _explicit_explorer_base_for_line(line: str) -> str | None:
    """Return explorer URL when ``line`` signals a chain.

    ``None`` means inherit sticky paragraph context.
    """

    m = _CHAIN_ID_HINT_RE.search(line)
    if m is not None:
        cid = int(m.group(1))
        if cid in _CHAIN_EXPLORER_BY_ID:
            return _CHAIN_EXPLORER_BY_ID[cid]
    ms = _STANDALONE_CHAIN_ID_RE.search(line)
    if ms is not None:
        return _CHAIN_EXPLORER_BY_ID[int(ms.group(1))]
    keyword_rules: list[tuple[re.Pattern[str], str]] = [
        (
            re.compile(r"(?i)\b(?:base(?:\s+mainnet|\s+l2|\s+l2:?)?|\bon\s+base\b)(?!\s*i/o)"),
            _CHAIN_EXPLORER_BY_ID[8453],
        ),
        (re.compile(r"(?i)\b(?:usdc\s+on\s+base\b)"), _CHAIN_EXPLORER_BY_ID[8453]),
        (
            re.compile(r"(?i)\b(?:arbitrum|arb\s+(?:mainnet|one))\b|\b42161\b"),
            _CHAIN_EXPLORER_BY_ID[42161],
        ),
        (
            re.compile(r"(?i)\b(?:optimism|op\s+(?:chain|stack|mainnet))\b"),
            _CHAIN_EXPLORER_BY_ID[10],
        ),
        (
            re.compile(r"(?i)\bpolygon\b|\b(?:matic\b)\s+(?:network|polygon)"),
            _CHAIN_EXPLORER_BY_ID[137],
        ),
        (
            re.compile(
                r"(?i)\b(?:bnb|bsc)\s+(?:smart\s+)?chain\b|\b(?:binance|bnb)\s+chain\b|\bbsc\b"
            ),
            _CHAIN_EXPLORER_BY_ID[56],
        ),
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
    """Wrap tx hashes and addresses in Telegram-safe ``<a>`` (input HTML already escaped)."""

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
    """Resolve the Telegram bot token from settings env or SecretStore vault path."""

    direct = (state.settings.telegram_bot_token or "").strip()
    if direct:
        return direct

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


def hosted_invoke_bundle_for_telegram_user(
    state: AureyServiceState,
    *,
    telegram_user_id: int | str | None,
) -> tuple[HostedSigningContext | None, dict[str, str]]:
    """Load hosted row once: optional signing context for ``ready`` users + ``hosted_wallet_address``.

    The wallet extra is included whenever ``wallet_address`` is persisted, even before ``ready``,
    so the model can reference a funding address during claim.
    """

    extras: dict[str, str] = {}
    if telegram_user_id is None:
        return None, extras
    cfg = state.settings
    if not cfg.hosted_platform_enabled:
        return None, extras
    factory = state.hosted_session_factory
    if factory is None:
        return None, extras

    from aurey.cloud.hosted_wallet_lookup import load_hosted_platform_user_row_for_telegram

    tid = int(telegram_user_id)
    db = factory()
    try:
        row = load_hosted_platform_user_row_for_telegram(
            db,
            cfg,
            telegram_user_id=tid,
            reason="telegram_invoke",
        )
        if row is None:
            return None, extras
        wa_raw = (row.wallet_address or "").strip()
        if wa_raw:
            try:
                extras["hosted_wallet_address"] = to_checksum_evm_address(wa_raw)
            except ValueError:
                pass
        sol_raw = (row.solana_wallet_address or "").strip()
        if sol_raw:
            extras["hosted_solana_wallet_address"] = sol_raw
        if (row.onboarding_state or "").strip() != "ready":
            return None, extras
        enc_raw = row.agent_api_key_encrypted
        enc_out = enc_raw.strip() if isinstance(enc_raw, str) and enc_raw.strip() else None
        leg_raw = row.agent_api_key
        leg_out = leg_raw.strip() if isinstance(leg_raw, str) and leg_raw.strip() else None
        ctx = HostedSigningContext(
            telegram_user_id=tid,
            user_agent_id=(row.user_agent_id or "").strip(),
            agent_api_key_encrypted=enc_out,
            agent_api_key_legacy_plaintext=leg_out,
            wallet_address=extras.get("hosted_wallet_address"),
        )
        return ctx, extras
    finally:
        db.close()


def hosted_signing_context_for_telegram_user(
    state: AureyServiceState,
    *,
    telegram_user_id: int | str | None,
) -> HostedSigningContext | None:
    """Load hosted DB row for an onboarding-ready user (``user_agent_id`` for 1Claw intents)."""

    signing, _ = hosted_invoke_bundle_for_telegram_user(
        state,
        telegram_user_id=telegram_user_id,
    )
    return signing


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

    session_id = f"telegram:{chat_id}"
    context: dict[str, Any] = {"telegram_chat_id": str(chat_id)}
    if user_id is not None:
        context["telegram_user_id"] = str(user_id)
    signing_ctx, hosted_ctx = hosted_invoke_bundle_for_telegram_user(
        state,
        telegram_user_id=user_id,
    )
    context.update(hosted_ctx)
    if hosted_ctx.get("hosted_wallet_address"):
        from aurey.service.invoke import HOSTED_WALLET_FROM_SERVER_CONTEXT_KEY

        context[HOSTED_WALLET_FROM_SERVER_CONTEXT_KEY] = True
    extras = [TelegramInvokeProgressCallback(progress_sink)] if progress_sink is not None else None
    result = invoke_deep_agent_turn(
        state,
        message=text,
        session_id=session_id,
        context=context,
        model=model,
        extra_callbacks=extras,
        hosted_signing_context=signing_ctx,
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
            "Telegram support requires the optional 'telegram' extra: pip install -e '.[telegram]'"
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
    gate_log = logging.getLogger("aurey.telegram.bot")

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id_raw = getattr(chat, "id", None)
        if await _telegram_handle_disallowed_chat_access_request(
            update,
            context,
            state=state,
            message_text=None,
        ):
            return
        tid_clear = getattr(update.effective_user, "id", None)
        if tid_clear is not None:
            await _telegram_clear_access_request_after_allowlist(
                state,
                telegram_user_id=int(tid_clear),
                user_data=getattr(context, "user_data", {}) or {},
            )
        cid_opt = int(chat_id_raw) if chat_id_raw is not None else None
        msg = update.effective_message
        if msg is None:
            return
        uid_log = getattr(update.effective_user, "id", None)
        gate_log.info("Telegram /start chat_id=%s telegram_user_id=%s", chat_id_raw, uid_log)
        cfg = state.settings
        db_url = (cfg.database_url or "").strip()
        if cfg.hosted_platform_enabled and db_url and state.hosted_session_factory is not None:
            from aurey.cloud.onboarding_refresh import refresh_hosted_user_claim_state
            from aurey.cloud.platform_client import (
                HostedPlatformApiError,
                OneClawPlatformClient,
            )
            from aurey.cloud.provision import (
                HostedAwaitingEmailFlow,
                HostedProvisioningError,
            )
            from aurey.custody.secret_store import OneClawHttpClient

            tg_user = update.effective_user
            tid_raw = getattr(tg_user, "id", None)
            if tid_raw is None:
                await msg.reply_text("Could not read your Telegram user id.")
                return
            telegram_user_id = int(tid_raw)
            telegram_username = getattr(tg_user, "username", None)

            def _provision_sync() -> tuple[str | None, str]:
                factory = state.hosted_session_factory
                if factory is None:
                    raise RuntimeError("hosted_session_factory is not configured.")
                platform = OneClawPlatformClient.from_settings(cfg)
                db = factory()
                try:
                    vault_http = (
                        state.runtime.oneclaw_evm_signer
                        if isinstance(state.runtime.oneclaw_evm_signer, OneClawHttpClient)
                        else None
                    )
                    from sqlalchemy import select

                    from aurey.cloud.models import HostedPlatformUserORM
                    from aurey.cloud.provision import (
                        ensure_hosted_telegram_row,
                        ensure_telegram_user_provisioned,
                    )

                    ensure_hosted_telegram_row(
                        db,
                        cfg,
                        telegram_user_id=telegram_user_id,
                        username=telegram_username,
                    )
                    row_snapshot = db.scalar(
                        select(HostedPlatformUserORM).where(
                            HostedPlatformUserORM.telegram_user_id == telegram_user_id,
                        ),
                    )
                    onboarding_snapshot = (
                        (row_snapshot.onboarding_state or "").strip()
                        if row_snapshot is not None
                        else ""
                    )

                    if cfg.hosted_require_verified_email and onboarding_snapshot in (
                        "awaiting_email",
                        "awaiting_email_verification",
                    ):
                        db.commit()
                        return None, onboarding_snapshot

                    row, _ = ensure_telegram_user_provisioned(
                        db,
                        cfg,
                        platform,
                        telegram_user_id=telegram_user_id,
                        username=telegram_username,
                        vault_http_client=vault_http,
                    )
                    try:
                        refresh_hosted_user_claim_state(db, cfg, platform, row)
                    except HostedPlatformApiError:
                        gate_log.warning(
                            "Hosted claim refresh failed after /start provision",
                            exc_info=False,
                        )
                    db.commit()
                    cu = (row.claim_url or "").strip()
                    return (cu or None), (row.onboarding_state or "").strip()
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()

            typing_done: asyncio.Event | None = None
            typing_task: asyncio.Task[None] | None = None
            if cid_opt is not None:
                typing_done, typing_task = _telegram_begin_typing_pump(context.bot, cid_opt)
            try:
                gate_log.info(
                    "Hosted /start provisioning begin telegram_user_id=%s",
                    telegram_user_id,
                )
                claim_url, onboard = await asyncio.to_thread(_provision_sync)
                gate_log.info(
                    "Hosted /start provisioning done telegram_user_id=%s onboard=%r",
                    telegram_user_id,
                    onboard,
                )
            except HostedAwaitingEmailFlow:
                sender = html.escape(cfg.hosted_email_sender_label, quote=False)
                await msg.reply_text(
                    "<b>Aurey</b>\n"
                    "Reply here with your <b>real email</b>. We send a verification code "
                    f"(from {sender}) — check spam/junk if it does not arrive, "
                    "then paste the digits here.",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return
            except HostedProvisioningError as exc:
                await msg.reply_text(f"setup failed (configuration): {exc}")
                return
            except HostedPlatformApiError as exc:
                await msg.reply_text(f"setup failed (platform): {exc}")
                return
            except Exception:
                gate_log.exception("Telegram hosted provisioning failed")
                await msg.reply_text("setup failed; see service logs for details.")
                return
            finally:
                if typing_done is not None and typing_task is not None:
                    await _telegram_end_typing_pump(typing_done, typing_task)

            if onboard == "ready":
                lines = [
                    "<b>Aurey</b>",
                    "You're all set — send a message to invoke the agent.",
                ]
                cu = (claim_url or "").strip()
                if cu:
                    lines.append("")
                    lines.append(
                        "If you still need your onboarding URL (it expires quickly): "
                        "open this link once to finish claiming."
                    )
                    lines.append(html.escape(cu, quote=False))
                await msg.reply_text(
                    "\n".join(lines),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return

            if onboard == "awaiting_email":
                sender = html.escape(cfg.hosted_email_sender_label, quote=False)
                await msg.reply_text(
                    "<b>Aurey</b>\n"
                    "Reply here with your <b>real email</b>. We send a verification code "
                    f"(from {sender}) — check spam/junk if it does not arrive, "
                    "then paste the digits here.",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return

            if onboard == "awaiting_email_verification":
                await msg.reply_text(
                    "<b>Aurey</b>\n"
                    "Check your inbox for the 6-digit code (and spam/junk if needed), then reply "
                    "with digits only. Send /cancel to begin again.",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return

            cu_eff = (claim_url or "").strip()
            if onboard == "awaiting_claim":
                if cfg.hosted_require_verified_email:
                    await msg.reply_text(
                        "<b>Aurey</b>\n"
                        "A fresh claim invite was emailed (check spam/junk). "
                        "<b>Claiming</b> secures your <b>agent credentials</b> and <b>wallet</b> on "
                        "1Claw claim page — you can do that anytime. "
                        "Send <b>/start</b> if you need another email.",
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    return
                if not cu_eff:
                    await msg.reply_text(
                        "setup still preparing — try /start again in a moment.",
                    )
                    return
                safe_url = html.escape(cu_eff, quote=False)
                text_lc = (
                    "<b>Aurey</b>\n"
                    "Your claim link is ready. <b>Claiming</b> means securing your "
                    "<b>wallet and agent credentials on 1Claw</b>  on that page:\n\n"
                    f"{safe_url}\n\n"
                    "After claiming, send messages here as usual."
                )
                await msg.reply_text(text_lc, parse_mode="HTML", disable_web_page_preview=True)
                return

            if onboard == "email_verified":
                await msg.reply_text(
                    "<b>Aurey</b> — finishing provisioning… tap /start again in a moment.",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return

            await msg.reply_text(
                f"Aurey onboarding state ({onboard!r}) — try /start again.",
                parse_mode=None,
            )
            return

        await msg.reply_text("Aurey is ready. Send a message to invoke the agent.")

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if msg is None or not msg.text:
            return
        chat = update.effective_chat
        user = update.effective_user
        chat_id_raw = getattr(chat, "id", None)
        if await _telegram_handle_disallowed_chat_access_request(
            update,
            context,
            state=state,
            message_text=msg.text,
        ):
            return
        chat_id_for_session = chat_id_raw if chat_id_raw is not None else "unknown"

        uid_msg = getattr(user, "id", None)
        hcfg = state.settings
        if (
            uid_msg is not None
            and hcfg.hosted_platform_enabled
            and (hcfg.database_url or "").strip()
        ):
            hosted_quick = await hosted_handle_optional_email_onboarding_chat(
                state=state,
                telegram_user_id=int(uid_msg),
                telegram_username=getattr(user, "username", None),
                text=msg.text,
            )
            if hosted_quick:
                await msg.reply_text(hosted_quick, parse_mode="HTML", disable_web_page_preview=True)
                return

        tid_for_gate = uid_msg
        if tid_for_gate is not None:
            block = await asyncio.to_thread(
                _hosted_user_must_finish_claim_message,
                state,
                telegram_user_id=int(tid_for_gate),
            )
            if block is not None:
                await msg.reply_text(block)
                return

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
        from telegram.error import BadRequest

        typing_done, typing_task = _telegram_begin_typing_pump(context.bot, chat_id_int)

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
            await _telegram_end_typing_pump(typing_done, typing_task)

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

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id_raw = getattr(chat, "id", None)
        if await _telegram_handle_disallowed_chat_access_request(
            update,
            context,
            state=state,
            message_text=None,
        ):
            return
        msg = update.effective_message
        if msg is None:
            return
        cfg = state.settings
        hosted_email = bool(
            cfg.hosted_platform_enabled
            and (cfg.database_url or "").strip()
            and cfg.hosted_require_verified_email
        )
        miniapp_pf = bool(cfg.telegram_miniapp_enabled and cfg.telegram_miniapp_launch_url())
        await msg.reply_text(
            _telegram_help_message_html(
                hosted_email_onboarding=hosted_email,
                miniapp_portfolio=miniapp_pf,
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    async def portfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id_raw = getattr(chat, "id", None)
        if await _telegram_handle_disallowed_chat_access_request(
            update,
            context,
            state=state,
            message_text=None,
        ):
            return
        msg = update.effective_message
        if msg is None:
            return
        cfg = state.settings
        if not cfg.telegram_miniapp_enabled:
            await msg.reply_text("Portfolio Web App is disabled on this deployment.")
            return
        url = cfg.telegram_miniapp_launch_url()
        if not url:
            await msg.reply_text("Portfolio Web App URL is not configured (operator).")
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Open portfolio", web_app=WebAppInfo(url=url))]]
        )
        await msg.reply_text(
            "Open your portfolio balances and DeFi positions in the Web App:",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

    async def cancel_hosted(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Clear pending Hosted email verification (COMMAND handler so /cancel is not swallowed)."""

        _ = context
        chat = update.effective_chat
        chat_id_raw = getattr(chat, "id", None)
        cid_opt = int(chat_id_raw) if chat_id_raw is not None else None
        user = update.effective_user
        uid_raw = getattr(user, "id", None)
        uid_opt = int(uid_raw) if uid_raw is not None else None
        allowed = _telegram_current_allowed_chat_ids(state)
        if not _telegram_chat_is_allowed(
            cid_opt,
            allowed,
            telegram_user_id=uid_opt,
        ):
            return
        msg = update.effective_message
        if msg is None:
            return
        if uid_raw is None:
            await msg.reply_text("Could not read your Telegram user id.")
            return
        sts = state.settings
        if not sts.hosted_platform_enabled or not (sts.database_url or "").strip():
            await msg.reply_text("Hosted email verification is inactive here.")
            return
        txt = await hosted_handle_optional_email_onboarding_chat(
            state=state,
            telegram_user_id=int(uid_raw),
            telegram_username=getattr(user, "username", None),
            text="/cancel",
        )
        if txt:
            await msg.reply_text(txt, parse_mode="HTML", disable_web_page_preview=True)

    hosted_email_onboarding = bool(
        state.settings.hosted_platform_enabled
        and (state.settings.database_url or "").strip()
        and state.settings.hosted_require_verified_email
    )
    miniapp_portfolio_cmds = bool(
        state.settings.telegram_miniapp_enabled
        and bool(state.settings.telegram_miniapp_launch_url())
    )

    async def _register_bot_commands(application: Application) -> None:
        try:
            await application.bot.set_my_commands(
                _telegram_bot_command_menu(
                    hosted_email_onboarding=hosted_email_onboarding,
                    miniapp_portfolio=miniapp_portfolio_cmds,
                ),
            )
        except Exception:
            gate_log.warning("Could not register Telegram bot commands menu", exc_info=True)

        mini_url = state.settings.telegram_miniapp_launch_url()
        if state.settings.telegram_miniapp_enabled and mini_url:
            try:
                from telegram import MenuButtonWebApp, WebAppInfo

                await application.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(
                        text="Portfolio",
                        web_app=WebAppInfo(url=mini_url),
                    ),
                )
            except Exception:
                gate_log.warning("Could not set Telegram Web App menu button", exc_info=True)

    app = (
        Application.builder()
        .token(bot_token)
        .post_init(_register_bot_commands)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("portfolio", portfolio_cmd))
    app.add_handler(CommandHandler("cancel", cancel_hosted))
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
    "hosted_invoke_bundle_for_telegram_user",
    "hosted_signing_context_for_telegram_user",
    "resolve_telegram_bot_token",
    "telegram_message_chunks",
]
