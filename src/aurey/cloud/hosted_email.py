"""SMTP delivery for Telegram hosted email verification and claim URLs."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import smtplib
import ssl
from email.message import EmailMessage, Message
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from aurey.cloud.hosted_email_templates import (
    HEADER_CID,
    claim_body_html,
    hosted_email_signature_plain,
    load_header_png_bytes,
    render_hosted_html_email,
    verification_body_html,
)
from aurey.settings import AureySettings

_log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,}$",
)


class HostedEmailError(RuntimeError):
    """Hosted outbound email cannot be sent (configuration or SMTP failure)."""


def normalize_contact_email(raw: str) -> str | None:
    """Return lowercased valid email address or ``None``.

    Mirrors common RFC-ish local syntax without full RFC 6531 / IDNA handling.
    """

    s = (raw or "").strip().lower()
    if not s or len(s) > 320:
        return None
    if _EMAIL_RE.match(s) is None:
        return None
    return s


def verification_code_challenge_hash(settings: AureySettings, code: str) -> str:
    """Deterministic SHA-256 hex digest for OTP storage."""

    pepper = (settings.hosted_email_code_pepper or "").strip()
    if not pepper:
        raise HostedEmailError(
            "AUREY_HOSTED_EMAIL_CODE_PEPPER must be set to send verification codes.",
        )
    joined = f"{pepper}:{(code or '').strip()}".encode()
    return hashlib.sha256(joined).hexdigest()


def generate_numeric_verification_code(*, digits: int = 6) -> str:
    """Cryptographically-strong numeric OTP."""

    hi = 10**digits - 1
    lo = 10 ** (digits - 1)
    return str(secrets.randbelow(hi - lo + 1) + lo)


def _assert_can_send_mail(settings: AureySettings, *, requires_pepper: bool) -> None:
    if not settings.hosted_email_smtp_configured():
        raise HostedEmailError(
            "Hosted email is not configured (set AUREY_HOSTED_SMTP_HOST and related SMTP vars).",
        )
    if requires_pepper and not settings.hosted_email_hmac_pepper_present():
        raise HostedEmailError(
            "AUREY_HOSTED_EMAIL_CODE_PEPPER must be set before sending verification email.",
        )


def build_hosted_email_message(
    settings: AureySettings,
    *,
    to_addrs: list[str],
    subject: str,
    text_body: str,
    html_body: str | None = None,
    branded_html: bool = False,
) -> Message:
    """Build plain, HTML, or related+inline-image MIME (stdlib-safe structure)."""

    frm = settings.hosted_email_from.strip()
    plain = text_body.rstrip() + hosted_email_signature_plain()
    header_jpeg = load_header_png_bytes() if branded_html else None

    if html_body is not None and branded_html and header_jpeg:
        wrapped = render_hosted_html_email(body_html=html_body, include_header=True)
        root = MIMEMultipart("related")
        root["Subject"] = subject
        root["From"] = frm
        root["To"] = ", ".join(to_addrs)
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(plain, "plain", "utf-8"))
        alt.attach(MIMEText(wrapped, "html", "utf-8"))
        root.attach(alt)
        img = MIMEImage(header_jpeg, _subtype="jpeg")
        img.add_header("Content-ID", f"<{HEADER_CID}>")
        img.add_header("Content-Disposition", "inline", filename="aurey-header.jpg")
        root.attach(img)
        return root

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = frm
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(plain)
    if html_body is not None:
        wrapped_plain = html_body
        if branded_html:
            wrapped_plain = render_hosted_html_email(
                body_html=html_body,
                include_header=False,
            )
        msg.add_alternative(wrapped_plain, subtype="html")
    return msg


def send_hosted_email(
    settings: AureySettings,
    *,
    to_addrs: list[str],
    subject: str,
    text_body: str,
    html_body: str | None = None,
    branded_html: bool = False,
) -> None:
    """Send a multipart email using stdlib SMTP."""

    _assert_can_send_mail(settings, requires_pepper=False)
    msg = build_hosted_email_message(
        settings,
        to_addrs=to_addrs,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        branded_html=branded_html,
    )

    host = settings.hosted_smtp_host.strip()
    port = settings.hosted_smtp_port
    user = settings.hosted_smtp_user.strip()
    password = settings.hosted_smtp_password
    tls_mode = settings.hosted_smtp_use_tls

    def _smtp_connect() -> smtplib.SMTP | smtplib.SMTP_SSL:
        ctx = ssl.create_default_context()
        if tls_mode == "ssl":
            return smtplib.SMTP_SSL(host, port, context=ctx)
        plain = smtplib.SMTP(host, port, timeout=25.0)
        if tls_mode == "starttls":
            plain.starttls(context=ctx)
        return plain

    try:
        with _smtp_connect() as smtp:
            if user or (password.strip() if isinstance(password, str) else str(password)):
                smtp.login(user, password.strip() if isinstance(password, str) else password)
            smtp.send_message(msg)
    except Exception as exc:
        _log.warning("SMTP send failed: %s", exc)
        raise HostedEmailError(f"SMTP send failed: {exc}") from exc


def send_verification_code_email(settings: AureySettings, *, to_email: str, code: str) -> None:
    """Email the OTP for inbox verification."""

    _assert_can_send_mail(settings, requires_pepper=True)
    ttl_min = max(1, settings.hosted_email_verification_ttl_seconds // 60)
    text_body = (
        f"Your Aurey verification code is: {code}\n\n"
        f"It expires in about {ttl_min} minutes.\n"
        "If you did not request this, ignore this email."
    )
    inner_html = verification_body_html(code=code, ttl_min=ttl_min)
    send_hosted_email(
        settings,
        to_addrs=[to_email.strip()],
        subject="Your Aurey verification code",
        text_body=text_body,
        html_body=inner_html,
        branded_html=True,
    )


def send_operator_new_registration_email(
    settings: AureySettings,
    *,
    to_email: str,
    user_email: str,
    telegram_handle: str,
    wallet_address_lines: list[str],
    telegram_user_id: int,
) -> None:
    """Notify the operator inbox that a user finished /start provisioning (pre-claim)."""

    _assert_can_send_mail(settings, requires_pepper=False)
    wallets_block = (
        "\n".join(wallet_address_lines)
        if wallet_address_lines
        else "(none returned from Platform yet)"
    )
    text_body = (
        "New Aurey user completed /start provisioning (Platform bootstrap; claim not required yet).\n\n"
        f"User email: {user_email}\n"
        f"Telegram: {telegram_handle}\n"
        f"Telegram user id: {telegram_user_id}\n\n"
        f"Wallet addresses created:\n{wallets_block}\n"
    )
    send_hosted_email(
        settings,
        to_addrs=[to_email.strip()],
        subject="Aurey: new user completed /start",
        text_body=text_body,
        html_body=None,
        branded_html=False,
    )


def send_claim_invite_email(
    settings: AureySettings,
    *,
    to_email: str,
    claim_url: str,
    display_hint: str | None = None,
) -> None:
    """Email the 1Claw claim URL (password setup happens on Platform after claim)."""

    _assert_can_send_mail(settings, requires_pepper=False)
    greeting = ""
    if display_hint:
        greeting = f"Hi {display_hint},\n\n"
    text_body = (
        f"{greeting}"
        "Claim your Hosted Aurey setup on 1Claw: take ownership of your login credentials "
        "(set a password on the claim page) and the wallet linked to your agent.\n\n"
        f"{claim_url}\n\n"
        "You can complete this whenever you are ready, then return to Telegram to chat.\n"
        "This link expires quickly—send /start in Telegram for a fresh claim email."
    )
    inner_html = claim_body_html(claim_url=claim_url, display_hint=display_hint)
    send_hosted_email(
        settings,
        to_addrs=[to_email.strip()],
        subject="Claim your Aurey wallet on 1Claw",
        text_body=text_body,
        html_body=inner_html,
        branded_html=True,
    )


__all__ = [
    "HostedEmailError",
    "generate_numeric_verification_code",
    "normalize_contact_email",
    "send_claim_invite_email",
    "send_operator_new_registration_email",
    "send_verification_code_email",
    "verification_code_challenge_hash",
]
