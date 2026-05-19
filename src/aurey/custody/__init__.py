"""Secret store and 1Claw custody primitives."""

from aurey.custody.errors import (
    CustodyError,
    EmptySecretValueError,
    OneClawSigningError,
    SecretNotFoundError,
    SecretStoreError,
    SecretStoreUnavailableError,
)
from aurey.custody.intents_models import IntentsSignTransactionRequest
from aurey.custody.secret_store import (
    FakeOneClawClient,
    FakeSecretStore,
    OneClawClient,
    OneClawEvmTransactionSigner,
    OneClawHttpClient,
    OneClawIntentsSignOnlyResult,
    OneClawPersonalSignResult,
    OneClawSecretStore,
    OneClawSignTransactionResult,
    OneClawTypedDataSignResult,
    SecretStore,
    SecretValue,
    delegated_subject_fingerprint,
)

__all__ = [
    "CustodyError",
    "delegated_subject_fingerprint",
    "EmptySecretValueError",
    "FakeOneClawClient",
    "FakeSecretStore",
    "IntentsSignTransactionRequest",
    "OneClawClient",
    "OneClawEvmTransactionSigner",
    "OneClawHttpClient",
    "OneClawIntentsSignOnlyResult",
    "OneClawPersonalSignResult",
    "OneClawSecretStore",
    "OneClawSignTransactionResult",
    "OneClawSigningError",
    "OneClawTypedDataSignResult",
    "SecretNotFoundError",
    "SecretStore",
    "SecretStoreError",
    "SecretStoreUnavailableError",
    "SecretValue",
]
