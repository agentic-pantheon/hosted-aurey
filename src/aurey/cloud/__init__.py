"""Hosted Aurey cloud integration (platform API, OIDC subject tokens, onboarding)."""

from __future__ import annotations

from aurey.cloud.onboarding.service import OnboardingService, TelegramStartOutcome

__all__ = ["OnboardingService", "TelegramStartOutcome"]
