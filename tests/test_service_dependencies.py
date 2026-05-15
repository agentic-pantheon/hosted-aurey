"""Tests for :mod:`aurey.service.dependencies` (no FastAPI import required)."""

from __future__ import annotations

from types import SimpleNamespace

from aurey.service.dependencies import get_aurey_service_state


def test_get_aurey_service_state_from_app_state():
    sentinel = object()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(aurey=sentinel)))
    assert get_aurey_service_state(req) is sentinel


def test_get_aurey_service_state_missing_attribute():
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert get_aurey_service_state(req) is None
