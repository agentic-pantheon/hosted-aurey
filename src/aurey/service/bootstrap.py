"""Build :class:`~aurey.service.state.AureyServiceState` from settings."""

from __future__ import annotations

from aurey.custody.caching_secret_store import CachingSecretStore
from aurey.custody.secret_store import OneClawHttpClient, OneClawSecretStore
from aurey.graphs.evm_tx_pipeline import Web3TxPipeline
from aurey.reasoning.checkpointer import (
    make_memory_checkpointer,
    open_postgres_checkpointer,
)
from aurey.runtime import AureyRuntime
from aurey.service.adapters import HttpxJsonClient, make_evm_rpc_factory, make_shared_httpx_client
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings
from aurey.util.ttl_lru_cache import TtlLruCache


class AureyServiceBootstrapError(RuntimeError):
    """Mandatory service wiring failed; messages must not contain secret values."""


def _validate_llm_bootstrap_configuration(s: AureySettings) -> None:
    """Fail fast when the LLM routing mode cannot succeed (never include secret values)."""

    mode = (s.llm_proxy or "shroud").strip()
    if mode == "direct":
        key = (s.openai_api_key or "").strip()
        if not key:
            raise AureyServiceBootstrapError(
                "Direct LLM mode is enabled (`AUREY_LLM_PROXY=direct`) but no OpenAI API key is "
                "configured (set `OPENAI_API_KEY`)."
            )
        return
    if mode == "shroud" and not s.hosted_platform_enabled:
        if not str(s.oneclaw_agent_id or "").strip():
            raise AureyServiceBootstrapError(
                "Shroud LLM mode on a standalone deployment requires `AUREY_ONECLAW_AGENT_ID` "
                "(the operator agent UUID)."
            )
        try:
            s.resolve_delegated_actor_api_key()
        except KeyError:
            raise AureyServiceBootstrapError(
                "Shroud LLM mode requires a usable operator/agent API key: set "
                "`AUREY_OPERATOR_AGENT_API_KEY` or ensure bootstrap key env is set "
                "(delegated-token actor falls back to bootstrap when operator key unset)."
            ) from None
        except ValueError as exc:
            raise AureyServiceBootstrapError(str(exc)) from exc


def bootstrap_aurey_service_state(settings: AureySettings | None = None) -> AureyServiceState:
    """Wire 1Claw secret store, runtime, and a LangGraph checkpointer (Postgres or in-memory)."""

    s = settings or AureySettings()
    if s.hosted_platform_enabled and not (s.database_url or "").strip():
        raise AureyServiceBootstrapError(
            "Hosted platform is enabled but no database URL is configured "
            "(set DATABASE_URL or AUREY_DATABASE_URL)."
        )
    vault_id = (s.oneclaw_vault_id or "").strip()
    if not vault_id:
        raise AureyServiceBootstrapError("1Claw vault id is not configured.")

    try:
        api_key = s.resolve_oneclaw_bootstrap_api_key()
    except KeyError:
        raise AureyServiceBootstrapError(
            "Bootstrap 1Claw API key is unavailable (bootstrap env var unset)."
        ) from None
    except ValueError:
        raise AureyServiceBootstrapError(
            "Bootstrap 1Claw API key configuration is invalid."
        ) from None

    _validate_llm_bootstrap_configuration(s)

    client = OneClawHttpClient(
        base_url=s.oneclaw_base_url.strip(),
        api_key=api_key,
        agent_token_expiry_skew_seconds=s.oneclaw_agent_token_expiry_skew_seconds,
        hosted_settings_for_ocv=s if s.hosted_platform_enabled else None,
    )
    store = OneClawSecretStore(client=client, vault_id=vault_id, agent_id=s.oneclaw_agent_id)
    secret_store = store
    ttl = float(s.secret_cache_ttl_seconds)
    if ttl > 0:
        secret_store = CachingSecretStore(store, ttl_s=ttl)

    httpx_client = make_shared_httpx_client()
    http_adapter = HttpxJsonClient(httpx_client)

    decimals_cache = TtlLruCache[tuple[str, str], int](
        maxsize=s.token_decimals_cache_maxsize,
        ttl_s=max(s.token_decimals_cache_ttl_seconds, 1.0),
    )

    runtime = AureyRuntime(
        settings=s,
        secret_store=secret_store,
        evm_rpc_factory=make_evm_rpc_factory(httpx_client),
        http=http_adapter,
        tx_pipeline=Web3TxPipeline(settings=s, secret_store=secret_store),
        oneclaw_evm_signer=client,
        hosted_session_factory=None,
        decimals_cache=decimals_cache,
    )

    default_model = (s.deep_agent_default_model or "").strip() or "openai:gpt-4o-mini"

    db_url = (s.database_url or "").strip()
    if db_url:
        try:
            pg = open_postgres_checkpointer(
                db_url,
                min_size=s.db_pool_min_size,
                max_size=s.db_pool_max_size,
            )
        except Exception as exc:
            raise AureyServiceBootstrapError(
                "PostgreSQL checkpointer could not be initialized."
            ) from exc
        from aurey.cloud.session import make_engine, make_session_factory
        from aurey.token_registry.repository import TokenRegistryRepository
        from aurey.token_registry.resolver import TokenResolver

        hosted_engine = make_engine(s)
        registry_session_factory = make_session_factory(hosted_engine)
        hosted_session_factory = (
            registry_session_factory if s.hosted_platform_enabled else None
        )
        token_resolver = TokenResolver(
            runtime=runtime,
            repository=TokenRegistryRepository(registry_session_factory),
        )
        if token_resolver is not None:
            runtime = AureyRuntime(
                settings=runtime.settings,
                secret_store=runtime.secret_store,
                evm_rpc_factory=runtime.evm_rpc_factory,
                http=runtime.http,
                tx_pipeline=runtime.tx_pipeline,
                oneclaw_evm_signer=runtime.oneclaw_evm_signer,
                lifi_base_url=runtime.lifi_base_url,
                prepared_txs=runtime.prepared_txs,
                decimals_cache=runtime.decimals_cache,
                token_resolver=token_resolver,
                hosted_session_factory=hosted_session_factory,
            )
        elif hosted_session_factory is not None:
            runtime = AureyRuntime(
                settings=runtime.settings,
                secret_store=runtime.secret_store,
                evm_rpc_factory=runtime.evm_rpc_factory,
                http=runtime.http,
                tx_pipeline=runtime.tx_pipeline,
                oneclaw_evm_signer=runtime.oneclaw_evm_signer,
                lifi_base_url=runtime.lifi_base_url,
                prepared_txs=runtime.prepared_txs,
                decimals_cache=runtime.decimals_cache,
                token_resolver=runtime.token_resolver,
                hosted_session_factory=hosted_session_factory,
            )
        return AureyServiceState(
            settings=s,
            runtime=runtime,
            checkpointer=pg.saver,
            default_model=default_model,
            hosted_session_factory=hosted_session_factory,
            _postgres=pg,
            _hosted_engine=hosted_engine,
            _httpx_client=httpx_client,
        )

    return AureyServiceState(
        settings=s,
        runtime=runtime,
        checkpointer=make_memory_checkpointer(),
        default_model=default_model,
        _httpx_client=httpx_client,
    )


__all__ = ["AureyServiceBootstrapError", "bootstrap_aurey_service_state"]
