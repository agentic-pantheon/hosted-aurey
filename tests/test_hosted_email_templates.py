"""Hosted onboarding email HTML wrappers."""

from __future__ import annotations

from aurey.cloud.hosted_email_templates import (
    AUREY_SITE_URL,
    AUREY_X_HANDLE,
    claim_body_html,
    hosted_email_signature_plain,
    load_header_png_bytes,
    render_hosted_html_email,
    verification_body_html,
)


def test_header_asset_bundled() -> None:
    data = load_header_png_bytes()
    assert data is not None
    assert len(data) > 1000
    assert data[:3] == b"\xff\xd8\xff"


def test_render_includes_signature_links() -> None:
    html = render_hosted_html_email(
        body_html=verification_body_html(code="123456", ttl_min=15),
        include_header=True,
    )
    assert "cid:aurey-header" in html
    assert AUREY_SITE_URL in html
    assert AUREY_X_HANDLE in html
    assert "123456" in html


def test_plain_signature() -> None:
    assert "aurey.agentic-pantheon.com" in hosted_email_signature_plain()
    assert "aurey_ai" in hosted_email_signature_plain()


def test_claim_body_escapes_hint() -> None:
    frag = claim_body_html(claim_url="https://claim.test/x", display_hint="<bad>")
    assert "&lt;bad&gt;" in frag
    assert "<bad>" not in frag


def test_build_branded_message_related_mime() -> None:
    from aurey.cloud.hosted_email import build_hosted_email_message
    from aurey.settings import AureySettings

    msg = build_hosted_email_message(
        AureySettings(hosted_email_from="test@example.com", hosted_email_from_name=""),
        to_addrs=["u@example.com"],
        subject="Test",
        text_body="plain",
        html_body=verification_body_html(code="111111", ttl_min=15),
        branded_html=True,
    )
    assert msg["From"] == "test@example.com"
    assert msg.get_content_type() == "multipart/related"
    types = [p.get_content_type() for p in msg.walk()]
    assert "multipart/alternative" in types
    assert "text/plain" in types
    assert "text/html" in types
    assert "image/jpeg" in types


def test_format_hosted_email_from_display_name() -> None:
    from aurey.cloud.hosted_email import build_hosted_email_message, format_hosted_email_from
    from aurey.settings import AureySettings

    s = AureySettings(
        hosted_email_from="fabri@agentic-pantheon.com",
        hosted_email_from_name="Fabri from Aurey",
    )
    assert format_hosted_email_from(s) == "Fabri from Aurey <fabri@agentic-pantheon.com>"
    msg = build_hosted_email_message(
        s,
        to_addrs=["u@example.com"],
        subject="Test",
        text_body="plain",
    )
    assert msg["From"] == "Fabri from Aurey <fabri@agentic-pantheon.com>"
