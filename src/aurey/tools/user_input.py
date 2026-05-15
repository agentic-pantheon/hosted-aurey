"""Host-visible user clarification queue (no secrets).

LangChain invokes sync tools inside an isolated ContextVar snapshot, so mutations to
:class:`contextvars.ContextVar` inside a tool body may not propagate to the caller thread.
:class:`threading.local` persists across structured tool invocations in the usual thread pools.
"""

from __future__ import annotations

from threading import local

from pydantic import BaseModel, Field

_tls = local()


class UserQuestion(BaseModel):
    """Single follow-up question for the user or host UI."""

    prompt: str = Field(min_length=1)
    id: str | None = None


class RequestUserInputArgs(BaseModel):
    questions: list[UserQuestion] = Field(
        min_length=1,
        description=(
            "Host-visible follow-up questions for missing wallet-operation fields (never for "
            "secrets or unrelated PII)."
        ),
    )


def _bucket() -> list[dict]:
    bucket = getattr(_tls, "pending", None)
    if bucket is None:
        bucket = []
        _tls.pending = bucket
    return bucket


def reset_user_input_context() -> None:
    """Clear queued questions for the current thread."""

    _tls.pending = []


def note_user_input_request(questions: list[UserQuestion]) -> int:
    """Append validated questions; return total queued in this thread."""

    bucket = _bucket()
    rows = [q.model_dump(exclude_none=False) for q in questions]
    # Drop None ids for stable JSON payloads when unset
    normalized: list[dict] = []
    for row in rows:
        cleaned = dict(row)
        if cleaned.get("id") is None:
            cleaned.pop("id", None)
        normalized.append(cleaned)
    bucket.extend(normalized)
    return len(bucket)


def get_pending_user_questions() -> list[dict]:
    """Return a shallow copy of queued questions (JSON-friendly)."""

    return list(_bucket())


__all__ = [
    "RequestUserInputArgs",
    "UserQuestion",
    "get_pending_user_questions",
    "note_user_input_request",
    "reset_user_input_context",
]
