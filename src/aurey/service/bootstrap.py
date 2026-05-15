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


def bootstrap_aurey_service_state(settings: AureySettings | None = None) -> AureyServiceState:
    """Wire 1Claw secret store, runtime, and a LangGraph checkpointer (Postgres or in-memory)."""

    s = settings or AureySettings()
    vault_id = (s.ocv_vault_id or "").strip()
    if not vault_id:
        raise AureyServiceBootstrapError("Operator 1Claw vault id is not configured.")

    try:
        api_key = s.resolve_ocv_agent_api_key()
    except KeyError:
        raise AureyServiceBootstrapError(
            "Operator 1Claw agent API key is unavailable (referenced env var unset)."
        ) from None
    except ValueError:
        raise AureyServiceBootstrapError(
            "Operator 1Claw agent API key configuration is invalid."
        ) from None

    client = OneClawHttpClient(
        base_url=s.ocv_oneclaw_base_url.strip(),
        api_key=api_key,
        agent_token_expiry_skew_seconds=s.ocv_agent_token_expiry_skew_seconds,
    )
    store = OneClawSecretStore(client=client, vault_id=vault_id, agent_id=s.ocv_agent_id)

    runtime = AureyRuntime(
        settings=s,
        secret_store=store,
        evm_rpc_factory=make_evm_rpc_factory(),
        http=UrllibHttpJsonClient(),
        tx_pipeline=Web3TxPipeline(settings=s, secret_store=store),
        oneclaw_evm_signer=client,
        oneclaw_operator_http=client,
    )

    default_model = (s.deep_agent_default_model or "").strip() or "openai:gpt-4o-mini"

    cloud_db_engine = None
    db_session_factory = None
    onboarding = None
    oidc_signer = None
    if s.cloud_onboarding_configured():
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from aurey.cloud.oidc import OidcSubjectTokenSigner
        from aurey.cloud.onboarding import OnboardingService
        from aurey.cloud.onboarding.grant_repository import SqlGrantReferenceRepository
        from aurey.cloud.platform import OneClawPlatformApiClient

        db_url = (s.database_url or "").strip()
        if not db_url:
            raise AureyServiceBootstrapError(
                "Cloud onboarding requires DATABASE_URL / AUREY_DATABASE_URL."
            )

        pem = s.resolve_oidc_rsa_private_key_pem_optional()
        plt_key = s.resolve_plt_app_api_key_optional()
        if pem is None:
            raise AureyServiceBootstrapError(
                "Cloud onboarding requires an RSA subject-token key PEM source."
            )
        if plt_key is None:
            raise AureyServiceBootstrapError(
                "Cloud onboarding requires a platform API key source."
            )

        cloud_db_engine = create_engine(db_url, pool_pre_ping=True)
        db_session_factory = sessionmaker(bind=cloud_db_engine, expire_on_commit=False)

        issuer = (s.oidc_issuer or "").strip().rstrip("/")
        audience = (s.subject_token_audience or "").strip() or (s.plt_app_id or "").strip()
        oidc_signer = OidcSubjectTokenSigner.from_pem(pem, issuer=issuer, default_audience=audience)

        platform_http = UrllibHttpJsonClient()
        platform = OneClawPlatformApiClient(
            base_url=s.plt_oneclaw_base_url.strip(),
            api_key=plt_key,
            http=platform_http,
        )
        grants = SqlGrantReferenceRepository()
        onboarding = OnboardingService(
            settings=s,
            session_factory=db_session_factory,
            platform=platform,
            oidc=oidc_signer,
            grant_repository=grants,
        )

    db_url = (s.database_url or "").strip()
    if db_url:
        try:
            pg = open_postgres_checkpointer(db_url)
        except Exception as exc:
            raise AureyServiceBootstrapError(
                "PostgreSQL checkpointer could not be initialized."
            ) from exc
        return AureyServiceState(
            settings=s,
            runtime=runtime,
            checkpointer=pg.saver,
            default_model=default_model,
            _postgres=pg,
            cloud_db_engine=cloud_db_engine,
            db_session_factory=db_session_factory,
            onboarding=onboarding,
            oidc_signer=oidc_signer,
        )

    return AureyServiceState(
        settings=s,
        runtime=runtime,
        checkpointer=make_memory_checkpointer(),
        default_model=default_model,
        cloud_db_engine=cloud_db_engine,
        db_session_factory=db_session_factory,
        onboarding=onboarding,
        oidc_signer=oidc_signer,
    )


__all__ = ["AureyServiceBootstrapError", "bootstrap_aurey_service_state"]
