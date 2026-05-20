"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "preserve_llm_env: skip autouse monkeypatch so LLM env reflects test intent",
    )


@pytest.fixture(autouse=True)
def _llm_direct_with_fake_openai_key_for_tests(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Avoid forcing every test runtime to bundle full Shroud + ``oneclaw_agent_id``.

    Modules that validate ``AUREY_LLM_PROXY`` defaults declare ``preserve_llm_env``.
    """

    if request.node.get_closest_marker("preserve_llm_env"):
        return
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-suite-openai-placeholder")
    monkeypatch.setenv("AUREY_LLM_PROXY", "direct")
