"""Build :class:`~aurey.service.state.AureyServiceState` from settings."""

from __future__ import annotations

from aurey.custody.secret_store import OneClawHttpClient, OneClawSecretStore
from aurey.graphs.evm_tx_pipeline import Web3TxPipeline
from aurey.reasoning.checkpointer import (
    make_memory_checkpointer,
    open_postgres_checkpointer,
)
from aurey.runtime import AureyRuntime
from aurey.service.adapters import UrllibHttpJsonClient, make_evm_rpc_factory
from aurey.service.state import AureyServiceState
from aurey.settings import AureySettings


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

    runtime = AureyRuntime(
        settings=s,
        secret_store=store,
        evm_rpc_factory=make_evm_rpc_factory(),
        http=UrllibHttpJsonClient(),
        tx_pipeline=Web3TxPipeline(settings=s, secret_store=store),
        oneclaw_evm_signer=client,
    )

    default_model = (s.deep_agent_default_model or "").strip() or "openai:gpt-4o-mini"

    db_url = (s.database_url or "").strip()
    if db_url:
        try:
            pg = open_postgres_checkpointer(db_url)
        except Exception as exc:
            raise AureyServiceBootstrapError(
                "PostgreSQL checkpointer could not be initialized."
            ) from exc
        hosted_session_factory = None
        hosted_engine = None
        if s.hosted_platform_enabled:
            from aurey.cloud.session import make_engine, make_session_factory

            hosted_engine = make_engine(s)
            hosted_session_factory = make_session_factory(hosted_engine)
        return AureyServiceState(
            settings=s,
            runtime=runtime,
            checkpointer=pg.saver,
            default_model=default_model,
            hosted_session_factory=hosted_session_factory,
            _postgres=pg,
            _hosted_engine=hosted_engine,
        )

    return AureyServiceState(
        settings=s,
        runtime=runtime,
        checkpointer=make_memory_checkpointer(),
        default_model=default_model,
    )


__all__ = ["AureyServiceBootstrapError", "bootstrap_aurey_service_state"]
