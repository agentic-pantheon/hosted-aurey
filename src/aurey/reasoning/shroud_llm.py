"""1Claw Shroud LLM proxy: OpenAI-compatible chat via ``X-Shroud-*`` headers.

See https://docs.1claw.xyz/docs/guides/shroud .
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from aurey.cloud.hosted_credentials import resolve_hosted_ocv_for_signing
from aurey.cloud.signing_context import HostedSigningContext
from aurey.runtime import AureyRuntime
from aurey.settings import AureySettings

# Extend when adding Anthropic / Google native paths alongside ``ChatOpenAI``.
_SUPPORTED_SHROUD_PROVIDERS_CHAT_OPENAI = frozenset({"openai"})

_SHROUD_PLACEHOLDER_OPENAI_API_KEY = "shroud"
_log = logging.getLogger(__name__)


class ShroudOutboundLoggingChatOpenAI(ChatOpenAI):
    """Emit one INFO log per completions request (generate or stream paths)."""

    def _http_base_url_for_logs(self) -> str:
        """Readable ``…/v1`` base LangChain/OpenAI SDK use internally (not ``OpenAI.__repr__``)."""

        configured = getattr(self, "openai_api_base", None)
        if configured not in (None, ""):
            return str(configured).rstrip("/")

        # ``root_*_client``: ``openai.OpenAI`` — log ``.base_url``, not ``repr(client)``.
        for attr in ("root_client", "root_async_client"):
            client_obj = getattr(self, attr, None)
            if client_obj is None:
                continue
            inner = getattr(client_obj, "base_url", None)
            if inner not in (None, ""):
                return str(inner).rstrip("/")

        return ""

    def _log_outbound_dispatch(self, call_kind: str) -> None:
        base_s = self._http_base_url_for_logs() or "(unknown)"

        model_s = getattr(self, "model_name", None) or getattr(self, "model", "") or ""
        provider_hdr = ""
        dh = getattr(self, "default_headers", None) or {}
        if isinstance(dh, dict):
            provider_hdr = str(dh.get("X-Shroud-Provider") or "")
        spec = (
            f"{provider_hdr}:{model_s}".lstrip(":") if provider_hdr else model_s
        ).strip()

        _log.info(
            "Shroud chat.completions outbound (%s) base_url=%s model_spec=%s",
            call_kind,
            base_s,
            spec or model_s,
        )

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        self._log_outbound_dispatch("sync_generate")
        return super()._generate(*args, **kwargs)

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        self._log_outbound_dispatch("async_generate")
        return await super()._agenerate(*args, **kwargs)

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        self._log_outbound_dispatch("sync_stream")
        yield from super()._stream(*args, **kwargs)

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        self._log_outbound_dispatch("async_stream")
        async for chunk in super()._astream(*args, **kwargs):
            yield chunk


# Prefer patching this alias in tests (see ``tests/test_shroud_llm.py``).
_SHROUD_CHAT_MODEL_FACTORY = ShroudOutboundLoggingChatOpenAI


@dataclass(frozen=True)
class ShroudAgentCredentials:
    """Never log ``api_key`` (``ocv_`` or operator key material)."""

    agent_id: str
    api_key: str


def parse_model_spec(spec: str) -> tuple[str, str]:
    """Split ``provider:model`` → ``(provider, model_id)`` (provider lowercased)."""

    s = (spec or "").strip()
    if not s:
        raise ValueError("Deep agent model spec must not be empty.")
    if ":" not in s:
        raise ValueError(
            "Model spec must use `provider:model` form (e.g. `openai:gpt-4o-mini`)."
        )
    provider_raw, _, rest = s.partition(":")
    provider_l = provider_raw.strip().lower()
    model_id = rest.strip()
    if not model_id:
        raise ValueError(f"Invalid model spec {spec!r}: missing model identifier after `:`.")
    if provider_l not in _SUPPORTED_SHROUD_PROVIDERS_CHAT_OPENAI:
        raise ValueError(
            "Aurey's Shroud LLM wiring currently supports only OpenAI-chat specs "
            f"(`openai:*`); unsupported provider {provider_l!r} in {spec!r}."
        )
    return provider_l, model_id


def resolve_shroud_agent_credentials_standalone(runtime: AureyRuntime) -> ShroudAgentCredentials:
    aid = str(runtime.settings.oneclaw_agent_id or "").strip()
    if not aid:
        raise RuntimeError("Standalone Shroud LLM requires AUREY_ONECLAW_AGENT_ID.")
    api_key = runtime.settings.resolve_delegated_actor_api_key()
    return ShroudAgentCredentials(agent_id=aid, api_key=api_key)


def resolve_shroud_agent_credentials_hosted(
    runtime: AureyRuntime,
    hosted_ctx: HostedSigningContext,
) -> ShroudAgentCredentials | None:
    agent_id = (hosted_ctx.user_agent_id or "").strip()
    if not agent_id:
        return None
    signer = runtime.oneclaw_evm_signer
    if signer is None:
        return None
    ocv = resolve_hosted_ocv_for_signing(
        runtime.settings,
        signer,
        agent_id=agent_id,
        ciphertext=hosted_ctx.agent_api_key_encrypted,
        legacy_plaintext=hosted_ctx.agent_api_key_legacy_plaintext,
    )
    raw = (ocv or "").strip()
    if not raw:
        return None
    return ShroudAgentCredentials(agent_id=agent_id, api_key=raw)


def resolve_shroud_provider_api_key_header(
    settings: AureySettings,
    *,
    vault_id_override: str | None = None,
) -> str | None:
    """Optional ``X-Shroud-Api-Key`` (plaintext OpenAI key or vault reference)."""

    plain = (settings.openai_api_key or "").strip()
    if plain:
        return plain
    path_raw = settings.openai_api_secret_path
    path = "" if path_raw is None else str(path_raw).strip().strip("/")
    if not path:
        return None
    vid = (vault_id_override or "").strip() or (settings.oneclaw_vault_id or "").strip()
    if not vid:
        return None
    # Path segments should stay stable in vault URLs; encode per RFC.
    escaped = quote(path, safe="/")
    return f"vault://{vid}/{escaped}"


def build_shroud_chat_model(
    settings: AureySettings,
    *,
    credentials: ShroudAgentCredentials,
    provider: str,
    model_id: str,
    vault_id_for_header: str | None = None,
) -> BaseChatModel:
    base = settings.shroud_base_url.strip().rstrip("/")
    x_api = resolve_shroud_provider_api_key_header(
        settings,
        vault_id_override=vault_id_for_header or settings.oneclaw_vault_id,
    )
    headers: dict[str, str] = {
        "X-Shroud-Agent-Key": f"{credentials.agent_id}:{credentials.api_key}",
        "X-Shroud-Provider": provider,
        "X-Shroud-Model": model_id,
    }
    if x_api:
        headers["X-Shroud-Api-Key"] = x_api
    return _SHROUD_CHAT_MODEL_FACTORY(
        api_key=_SHROUD_PLACEHOLDER_OPENAI_API_KEY,
        base_url=f"{base}/v1",
        model=model_id,
        default_headers=headers,
    )


def build_direct_chat_model(settings: AureySettings, *, model_id: str) -> BaseChatModel:
    key = (settings.openai_api_key or "").strip()
    if not key:
        raise RuntimeError(
            "Direct LLM mode requires OPENAI_API_KEY (or pass a plaintext key via settings)."
        )
    return ChatOpenAI(api_key=key, model=model_id)


def resolve_llm_chat_model_for_graph(
    runtime: AureyRuntime,
    *,
    model_spec: str,
    hosted_signing_context: HostedSigningContext | None = None,
) -> BaseChatModel:
    """Produce a LangChain chat model for Deep Agents according to LLM routing settings."""

    s = runtime.settings
    provider, model_id = parse_model_spec(model_spec)
    if s.llm_proxy == "direct":
        return build_direct_chat_model(s, model_id=model_id)

    credentials: ShroudAgentCredentials | None = None
    if hosted_signing_context is None:
        credentials = resolve_shroud_agent_credentials_standalone(runtime)
    else:
        credentials = resolve_shroud_agent_credentials_hosted(runtime, hosted_signing_context)

    if credentials is None:
        raise RuntimeError("Shroud LLM credentials could not be resolved for this request.")

    vault_for_header = s.oneclaw_vault_id
    return build_shroud_chat_model(
        s,
        credentials=credentials,
        provider=provider,
        model_id=model_id,
        vault_id_for_header=vault_for_header.strip() if vault_for_header else None,
    )


def hosted_shroud_llm_credentials_ready(
    runtime: AureyRuntime,
    hosted_ctx: HostedSigningContext,
) -> bool:
    """Cheap pre-flight for Telegram turns (clear error without graph compile failures)."""

    return resolve_shroud_agent_credentials_hosted(runtime, hosted_ctx) is not None


__all__ = [
    "ShroudAgentCredentials",
    "ShroudOutboundLoggingChatOpenAI",
    "build_direct_chat_model",
    "build_shroud_chat_model",
    "hosted_shroud_llm_credentials_ready",
    "parse_model_spec",
    "resolve_llm_chat_model_for_graph",
    "resolve_shroud_agent_credentials_hosted",
    "resolve_shroud_agent_credentials_standalone",
    "resolve_shroud_provider_api_key_header",
]
