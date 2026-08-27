"""Application configuration and environment loading.

All configuration arrives through environment variables. Secrets are held as
``SecretStr`` so they cannot be rendered by accident in logs, tracebacks or API
responses (SECURITY.md section 6, AI_DEVELOPMENT_RULES.md section 5).
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class ModelGatewayProvider(StrEnum):
    GENAILAB = "genailab"
    MOCK = "mock"


class AuthMode(StrEnum):
    DEVELOPMENT = "development"
    OIDC = "oidc"


CsvList = Annotated[list[str], Field(default_factory=list)]


class Settings(BaseSettings):
    """Runtime configuration.

    Field names match the variables documented in ``.env.example``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
    )

    # --- Application -------------------------------------------------------
    app_name: str = "manufacturing-ai-cost-platform"
    app_env: AppEnv = AppEnv.DEVELOPMENT
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # --- Database ----------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/platform.db"
    database_echo: bool = False
    sqlite_busy_timeout_ms: int = 5000

    # --- Redis -------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = True

    # --- Model gateway -----------------------------------------------------
    model_gateway_provider: ModelGatewayProvider = ModelGatewayProvider.MOCK

    # --- GenAILab ----------------------------------------------------------
    genai_base_url: str = "https://genailab.tcs.in/v1"
    genai_api_key: SecretStr = SecretStr("")
    genai_timeout_seconds: float = 60.0
    ssl_verify: bool = False
    allow_insecure_tls: bool = False

    # Resilience (SECURITY.md section 19). Retrying is owned by the gateway
    # wrapper, not the provider SDK, so these are the only retry knobs.
    genai_max_attempts: int = 3
    genai_retry_base_delay_seconds: float = 0.5
    genai_retry_max_delay_seconds: float = 8.0
    genai_retry_max_elapsed_seconds: float = 60.0

    genai_circuit_breaker_enabled: bool = True
    genai_circuit_breaker_failure_threshold: int = 5
    genai_circuit_breaker_reset_seconds: float = 30.0

    # Live smoke test. Off unless explicitly enabled; guards against a test run
    # accidentally spending money (AI_DEVELOPMENT_RULES.md section 25).
    genai_smoke_test_enabled: bool = False
    genai_smoke_test_model: str = ""

    # --- Authentication (SECURITY.md section 3) ----------------------------
    auth_mode: AuthMode = AuthMode.DEVELOPMENT

    # JWT validation. These apply to both adapters; only the key source differs.
    jwt_algorithm: str = "HS256"
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_leeway_seconds: float = 0.0
    jwt_access_token_ttl_seconds: int = 900

    # Claim mapping. Tenant and roles are not standardised by OIDC, so the
    # claim names a provider uses are configuration, not an assumption.
    jwt_tenant_claim: str = "tenant_id"
    jwt_roles_claim: str = "roles"

    # OIDC seam. Present so a deployment has somewhere to put these values;
    # the adapter itself is not implemented.
    oidc_issuer: str | None = None
    oidc_jwks_url: str | None = None
    oidc_audience: str | None = None

    # --- Model registry ----------------------------------------------------
    # Seed data is configuration, not code. Point this at a different file to
    # register a different model set without changing the application.
    model_registry_seed_path: str = "app/db/seed/genailab_models.json"
    #: Registers seeded models that are not present yet. Never overwrites an
    #: existing row, so operator-supplied pricing survives a redeploy.
    model_registry_seed_on_startup: bool = True

    # --- Cost and budgets --------------------------------------------------
    #: The single currency every aggregate is reported in
    #: (DATABASE_SCHEMA.md section 15). Cost events recorded in another currency
    #: are converted through CURRENCY_RATES before aggregation, or reported as
    #: unconvertible — never summed in blind.
    platform_base_currency: str = "USD"
    #: Conversion rates *into* the base currency, as CODE:RATE pairs, e.g.
    #: "INR:0.012,EUR:1.08". Conversion is configuration, not business logic;
    #: an unlisted currency is refused rather than guessed.
    currency_rates: dict[str, float] = Field(default_factory=dict)

    # --- Rate limiting (SECURITY.md section 18) ----------------------------
    # Applied to the expensive endpoint. Per-process only; see PRODUCTION_READINESS.md.
    rate_limit_enabled: bool = True
    ai_execute_rate_limit_requests: int = 60
    ai_execute_rate_limit_window_seconds: float = 60.0

    # --- API security ------------------------------------------------------
    cors_allow_origins: CsvList
    max_request_bytes: int = 10 * 1024 * 1024

    # ----------------------------------------------------------------- utils
    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept comma-separated strings for list-valued settings."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("currency_rates", mode="before")
    @classmethod
    def _parse_currency_rates(cls, value: object) -> object:
        """Accept ``CODE:RATE,CODE:RATE`` from the environment.

        A malformed entry raises rather than being skipped: a silently dropped
        rate would turn into an unconvertible budget at runtime, far from the
        typo that caused it.
        """
        if not isinstance(value, str):
            return value
        rates: dict[str, float] = {}
        for pair in value.split(","):
            entry = pair.strip()
            if not entry:
                continue
            code, separator, rate = entry.partition(":")
            if not separator:
                raise ValueError(
                    f"CURRENCY_RATES entry {entry!r} must be CODE:RATE, e.g. INR:0.012"
                )
            try:
                rates[code.strip().upper()] = float(rate)
            except ValueError as exc:
                raise ValueError(
                    f"CURRENCY_RATES rate for {code.strip()!r} is not a number: {rate!r}"
                ) from exc
        return rates

    @field_validator("platform_base_currency")
    @classmethod
    def _normalise_base_currency(cls, value: str) -> str:
        currency = value.strip().upper()
        if not currency:
            raise ValueError("PLATFORM_BASE_CURRENCY must be set")
        return currency

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        level = value.upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @model_validator(mode="after")
    def _enforce_production_guards(self) -> Settings:
        """Fail fast on configurations that are unsafe in production.

        These guards implement the production expectations already stated in
        ARCHITECTURE.md section 7, SECURITY.md section 7 and
        AI_DEVELOPMENT_RULES.md section 5. They are deliberately startup-time
        errors rather than warnings.
        """
        if self.app_env is AppEnv.PRODUCTION:
            if not self.ssl_verify and not self.allow_insecure_tls:
                raise ValueError(
                    "SSL_VERIFY=false is not permitted when APP_ENV=production. "
                    "Prefer SSL_VERIFY=true with the approved internal CA. To "
                    "record a deliberate, documented security exception set "
                    "ALLOW_INSECURE_TLS=true."
                )
            if self.auth_mode is AuthMode.DEVELOPMENT:
                raise ValueError(
                    "AUTH_MODE=development is not permitted when "
                    "APP_ENV=production. Configure an enterprise OIDC adapter."
                )
            if self.debug:
                raise ValueError("DEBUG=true is not permitted when APP_ENV=production.")

        if self.auth_mode is AuthMode.DEVELOPMENT:
            secret = self.jwt_secret.get_secret_value()
            if not secret:
                raise ValueError(
                    "JWT_SECRET must be set when AUTH_MODE=development. Tokens "
                    "are genuinely signature-verified; there is no unsigned "
                    "fallback."
                )
            # RFC 7518 section 3.2: an HMAC key must be at least as long as the
            # hash output. A shorter symmetric secret is brute-forceable, and
            # anyone who recovers it can mint tokens for any tenant.
            if (
                self.jwt_algorithm.upper().startswith("HS")
                and len(secret.encode()) < 32
            ):
                raise ValueError(
                    "JWT_SECRET must be at least 32 bytes for HMAC algorithms "
                    "(RFC 7518 section 3.2). Generate one with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(48))"'
                )

        if self.jwt_algorithm.upper() == "NONE":
            # Refused outright. `alg: none` disables signature verification.
            raise ValueError("JWT_ALGORITHM=none is never permitted.")

        if (
            self.model_gateway_provider is ModelGatewayProvider.GENAILAB
            and not self.genai_api_key.get_secret_value()
        ):
            raise ValueError(
                "GENAI_API_KEY must be set when MODEL_GATEWAY_PROVIDER=genailab."
            )
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def safe_dump(self) -> dict[str, object]:
        """Configuration snapshot with every secret redacted.

        Safe to log or return from a diagnostic endpoint.
        """
        data = self.model_dump(mode="json")
        for key, value in list(data.items()):
            is_secret_field = isinstance(getattr(self, key, None), SecretStr)
            if is_secret_field or (isinstance(value, str) and _looks_secret(key)):
                data[key] = "***redacted***"
        return data


_SECRET_HINTS = ("key", "secret", "token", "password", "credential", "authorization")


def _looks_secret(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor used by the application and its dependencies."""
    return Settings()
