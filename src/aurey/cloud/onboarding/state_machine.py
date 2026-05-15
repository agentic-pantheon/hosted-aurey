"""Strict onboarding phase transitions (Phase C)."""

from __future__ import annotations

from aurey.cloud.db.models import OnboardingPhase

_ALLOWED: dict[OnboardingPhase, frozenset[OnboardingPhase]] = {
    OnboardingPhase.PENDING: frozenset({OnboardingPhase.AWAITING_CLAIM}),
    OnboardingPhase.AWAITING_CLAIM: frozenset({OnboardingPhase.READY}),
    OnboardingPhase.READY: frozenset(),
}


class InvalidOnboardingTransition(ValueError):
    """Raised when a state change is not permitted for the hosted onboarding flow."""


def assert_transition_allowed(*, from_phase: OnboardingPhase, to_phase: OnboardingPhase) -> None:
    if from_phase == to_phase:
        return
    allowed = _ALLOWED.get(from_phase, frozenset())
    if to_phase not in allowed:
        raise InvalidOnboardingTransition(
            f"Onboarding transition not allowed: {from_phase.value!r} -> {to_phase.value!r}"
        )


def coerce_phase_value(raw: str | None) -> OnboardingPhase:
    """Map unknown/empty DB strings to ``PENDING`` (cloud-first, no legacy quirks)."""

    if not raw:
        return OnboardingPhase.PENDING
    try:
        return OnboardingPhase(str(raw))
    except ValueError:
        return OnboardingPhase.PENDING


__all__ = ["InvalidOnboardingTransition", "assert_transition_allowed", "coerce_phase_value"]
