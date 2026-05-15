"""Secret store and 1Claw custody primitives."""

from aurey.custody.errors import (
    CustodyError,
    EmptySecretValueError,
    OneClawSigningError,
    SecretNotFoundError,
    SecretStoreError,
    SecretStoreUnavailableError,
)
from aurey.custody.secret_store import (
    FakeOneClawClient,
    FakeSecretStore,
    OneClawClient,
    OneClawEvmTransactionSigner,
    OneClawHttpClient,
    OneClawSecretStore,
    OneClawSignTransactionResult,
    SecretStore,
    SecretValue,
)

__all__ = [
    "CustodyError",
    "EmptySecretValueError",
    "FakeOneClawClient",
    "FakeSecretStore",
    "OneClawClient",
    "OneClawEvmTransactionSigner",
    "OneClawHttpClient",
    "OneClawSecretStore",
    "OneClawSignTransactionResult",
    "OneClawSigningError",
    "SecretNotFoundError",
    "SecretStore",
    "SecretStoreError",
    "SecretStoreUnavailableError",
    "SecretValue",
]
