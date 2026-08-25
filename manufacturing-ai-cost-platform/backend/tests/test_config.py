"""Configuration tests.

Covers defaults, parsing, secret handling and the production safety guards.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import (
    AppEnv,
    AuthMode,
    ModelGatewayProvider,
    Settings,
    get_settings,
)

# Not a credential. A non-secret sentinel used to prove that secret handling
# redacts whatever it is given.
FAKE_SECRET_VALUE = "unit-test-sentinel-value"


def _base(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_env": AppEnv.DEVELOPMENT,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "redis_enabled": False,
        "model_gateway_provider": ModelGatewayProvider.MOCK,
        "auth_mode": AuthMode.DEVELOPMENT,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_api_prefix_matches_contract() -> None:
    """The contract declares servers: /api/v1."""
    assert _base().api_v1_prefix == "/api/v1"


def test_csv_settings_are_split() -> None:
    settings = _base(
        cors_allow_origins="http://a.test, http://b.test",
        dev_principal_roles="ADMIN,ANALYST",
    )
    assert settings.cors_allow_origins == ["http://a.test", "http://b.test"]
    assert settings.dev_principal_roles == ["ADMIN", "ANALYST"]


def test_log_level_is_normalised_and_validated() -> None:
    assert _base(log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValidationError):
        _base(log_level="chatty")


def test_is_sqlite_detects_the_mvp_database() -> None:
    assert _base().is_sqlite is True
    assert _base(database_url="postgresql+asyncpg://h/db").is_sqlite is False


# --------------------------------------------------------------------- secrets
def test_api_key_is_a_secret_and_not_rendered() -> None:
    settings = _base(genai_api_key=FAKE_SECRET_VALUE)
    assert isinstance(settings.genai_api_key, SecretStr)
    assert FAKE_SECRET_VALUE not in repr(settings)
    assert FAKE_SECRET_VALUE not in str(settings.genai_api_key)
    # Retrievable only through the explicit accessor.
    assert settings.genai_api_key.get_secret_value() == FAKE_SECRET_VALUE


def test_safe_dump_redacts_secrets() -> None:
    dumped = _base(genai_api_key=FAKE_SECRET_VALUE).safe_dump()
    assert dumped["genai_api_key"] == "***redacted***"
    assert FAKE_SECRET_VALUE not in str(dumped)


# ---------------------------------------------------------- production guards
def test_production_rejects_disabled_tls_without_exception() -> None:
    with pytest.raises(ValidationError, match="SSL_VERIFY"):
        _base(
            app_env=AppEnv.PRODUCTION,
            ssl_verify=False,
            auth_mode=AuthMode.OIDC,
            debug=False,
        )


def test_production_allows_disabled_tls_with_documented_exception() -> None:
    settings = _base(
        app_env=AppEnv.PRODUCTION,
        ssl_verify=False,
        allow_insecure_tls=True,
        auth_mode=AuthMode.OIDC,
        debug=False,
    )
    assert settings.allow_insecure_tls is True


def test_production_rejects_the_development_auth_adapter() -> None:
    with pytest.raises(ValidationError, match="AUTH_MODE"):
        _base(app_env=AppEnv.PRODUCTION, ssl_verify=True, auth_mode=AuthMode.DEVELOPMENT)


def test_production_rejects_debug() -> None:
    with pytest.raises(ValidationError, match="DEBUG"):
        _base(
            app_env=AppEnv.PRODUCTION,
            ssl_verify=True,
            auth_mode=AuthMode.OIDC,
            debug=True,
        )


def test_genailab_provider_requires_an_api_key() -> None:
    with pytest.raises(ValidationError, match="GENAI_API_KEY"):
        _base(model_gateway_provider=ModelGatewayProvider.GENAILAB, genai_api_key="")


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
