"""HTML/plain wrappers for hosted onboarding mail (verification + claim)."""

from __future__ import annotations

import html
from importlib.resources import files

AUREY_SITE_URL = "https://aurey.agentic-pantheon.com"
AUREY_X_URL = "https://x.com/aurey_ai"
AUREY_X_HANDLE = "@aurey_ai"
HEADER_CID = "aurey-header"


def hosted_email_signature_plain() -> str:
    return (
        f"\n—\nAurey · {AUREY_SITE_URL}\n"
        f"X: {AUREY_X_HANDLE} ({AUREY_X_URL})\n"
    )


def load_header_png_bytes() -> bytes | None:
    """Bundled medal artwork for inline ``cid:`` header (optional if missing)."""

    try:
        return files("aurey.cloud.email_assets").joinpath("aurey-header.jpg").read_bytes()
    except Exception:
        return None


def render_hosted_html_email(*, body_html: str, include_header: bool) -> str:
    """Table-based layout for broad client support (Gmail, Outlook, Apple Mail)."""

    header_block = ""
    if include_header:
        header_block = (
            '<tr><td align="center" style="padding:24px 24px 8px 24px;">'
            f'<img src="cid:{HEADER_CID}" alt="Aurey" width="140" height="140" '
            'style="display:block;border:0;border-radius:50%;max-width:140px;height:auto;" />'
            "</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Aurey</title>
</head>
<body style="margin:0;padding:0;background-color:#12081f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#12081f;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
               style="max-width:560px;background:linear-gradient(165deg,#2a1540 0%,#1a0a2e 55%,#12081f 100%);border-radius:16px;border:1px solid #5c2d82;">
          {header_block}
          <tr>
            <td style="padding:8px 28px 20px 28px;color:#f3e8ff;font-size:16px;line-height:1.55;">
              {body_html}
            </td>
          </tr>
          <tr>
            <td style="padding:0 28px 28px 28px;border-top:1px solid #4a2866;">
              <p style="margin:16px 0 8px 0;font-size:13px;line-height:1.5;color:#c4b5fd;">
                <strong style="color:#fbbf24;">Aurey</strong> — agentic Pantheon
              </p>
              <p style="margin:0 0 6px 0;font-size:13px;line-height:1.5;">
                <a href="{html.escape(AUREY_SITE_URL, quote=True)}"
                   style="color:#fbbf24;text-decoration:none;">{html.escape(AUREY_SITE_URL)}</a>
              </p>
              <p style="margin:0;font-size:13px;line-height:1.5;">
                <a href="{html.escape(AUREY_X_URL, quote=True)}"
                   style="color:#fbbf24;text-decoration:none;">{html.escape(AUREY_X_HANDLE)}</a>
                on X
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def verification_body_html(*, code: str, ttl_min: int) -> str:
    safe_code = html.escape(code.strip())
    return (
        "<p style=\"margin:0 0 12px 0;color:#e9d5ff;\">Your verification code for "
        "<strong style=\"color:#fbbf24;\">Aurey</strong>:</p>"
        f'<p style="margin:0 0 16px 0;font-size:28px;letter-spacing:0.2em;font-weight:700;'
        f'color:#fbbf24;text-align:center;">{safe_code}</p>'
        f"<p style=\"margin:0 0 8px 0;color:#c4b5fd;\">Expires in about "
        f"<strong>{ttl_min}</strong> minute{'s' if ttl_min != 1 else ''}.</p>"
        "<p style=\"margin:12px 0 0 0;font-size:14px;color:#a78bfa;\">"
        "If you did not request this, you can ignore this email.</p>"
    )


def claim_body_html(*, claim_url: str, display_hint: str | None) -> str:
    escaped_url = html.escape(claim_url.strip(), quote=True)
    link_label = html.escape(claim_url.strip())
    greeting = ""
    if display_hint:
        greeting = (
            f'<p style="margin:0 0 12px 0;color:#e9d5ff;">Hi '
            f"<strong>{html.escape(display_hint.strip())}</strong>,</p>"
        )
    return (
        f"{greeting}"
        "<p style=\"margin:0 0 12px 0;color:#e9d5ff;\">"
        "<strong style=\"color:#fbbf24;\">Claim</strong> your Hosted Aurey setup on "
        "<strong style=\"color:#fbbf24;\">1Claw</strong>: take ownership of your "
        "<strong>login credentials</strong> (password on the claim page) and the "
        "<strong>wallet</strong> linked to your agent. Use this one-time link:</p>"
        '<p style="margin:0 0 16px 0;text-align:center;">'
        f'<a href="{escaped_url}" style="display:inline-block;padding:12px 22px;'
        "background:linear-gradient(90deg,#f59e0b,#fbbf24);color:#1a0a2e;"
        'font-weight:700;text-decoration:none;border-radius:8px;">Claim credentials &amp; wallet</a></p>'
        f'<p style="margin:0 0 12px 0;font-size:13px;color:#a78bfa;word-break:break-all;">'
        f"{link_label}</p>"
        "<p style=\"margin:0 0 8px 0;color:#c4b5fd;\">You can do this whenever you are ready, "
        "then return to Telegram to chat with Aurey.</p>"
        "<p style=\"margin:0;font-size:14px;color:#a78bfa;\">Links expire quickly — send "
        "<strong>/start</strong> in Telegram for a fresh claim email.</p>"
    )
