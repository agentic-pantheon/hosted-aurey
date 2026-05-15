"""Store only vault-style grant references and non-secret metadata (Phase C)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from aurey.cloud.db.models import PlatformUser


@runtime_checkable
class GrantReferenceRepository(Protocol):
    """Persistence port for delegated-grant pointers (never raw bearer tokens)."""

    def save_grant_reference(
        self,
        session: Session,
        *,
        user: PlatformUser,
        grant_ref_path: str,
        metadata: dict[str, Any],
    ) -> None:
        """Persist operator-relative vault path and opaque metadata."""


class SqlGrantReferenceRepository:
    """Updates :class:`~aurey.cloud.db.models.PlatformUser` grant columns."""

    def save_grant_reference(
        self,
        session: Session,
        *,
        user: PlatformUser,
        grant_ref_path: str,
        metadata: dict[str, Any],
    ) -> None:
        path = (grant_ref_path or "").strip()
        if not path:
            raise ValueError("grant_ref_path must not be empty.")
        user.grant_ref_path = path
        user.grant_metadata = dict(metadata)


__all__ = ["GrantReferenceRepository", "SqlGrantReferenceRepository"]
