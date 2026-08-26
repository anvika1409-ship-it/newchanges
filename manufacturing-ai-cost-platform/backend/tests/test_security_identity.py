"""Identity adapter tests.

Covers adapter selection, the development adapter's contract, and the OIDC seam.
"""

from __future__ import annotations

import pytest

from app.core.config import AppEnv, AuthMode, ModelGatewayProvider, Settings
from app.security.identity import (
    ClaimsMapper,
    DevelopmentIdentityAdapter,
    IdentityAdapter,
    OidcIdentityAdapter,
    build_identity_adapter,
)
from app.security.principal import Role, RoleAssignment, ScopeType
from app.security.tokens import (
    ExpiredTokenError,
    InvalidTokenError,
    JwtValidator,
    MissingTokenError,
    StaticSecretKeyResolver,
    TokenClaims,
)
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_JWT_SECRET

TENANT_A = "tenant-a"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_env": AppEnv.DEVELOPMENT,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "redis_enabled": False,
        "model_gateway_provider": ModelGatewayProvider.MOCK,
        "auth_mode": AuthMode.DEVELOPMENT,
        "jwt_secret": TEST_JWT_SECRET,
        "jwt_issuer": TEST_ISSUER,
        "jwt_audience": TEST_AUDIENCE,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


# ------------------------------------------------------------ adapter choice
def test_factory_returns_the_development_adapter() -> None:
    adapter = build_identity_adapter(_settings())
    assert isinstance(adapter, DevelopmentIdentityAdapter)
    assert adapter.mode is AuthMode.DEVELOPMENT


def test_factory_returns_the_oidc_adapter() -> None:
    adapter = build_identity_adapter(_settings(auth_mode=AuthMode.OIDC))
    assert isinstance(adapter, OidcIdentityAdapter)
    assert adapter.mode is AuthMode.OIDC


def test_both_adapters_implement_the_port() -> None:
    """The application depends on the port, not on either implementation."""
    for adapter in (
        build_identity_adapter(_settings()),
        build_identity_adapter(_settings(auth_mode=AuthMode.OIDC)),
    ):
        assert isinstance(adapter, IdentityAdapter)


# ---------------------------------------------------------------- OIDC seam
async def test_oidc_adapter_refuses_rather_than_faking_validation() -> None:
    """The seam must fail loudly.

    A stub that returned a principal without verifying the token would be a
    silent authentication bypass — far worse than an unimplemented adapter.
    """
    adapter = OidcIdentityAdapter(_settings(auth_mode=AuthMode.OIDC))
    with pytest.raises(NotImplementedError, match="not implemented"):
        await adapter.authenticate("any-token")


def test_oidc_adapter_has_no_token_minting_capability() -> None:
    """Only the development adapter can mint tokens; a real IdP issues them."""
    adapter = OidcIdentityAdapter(_settings(auth_mode=AuthMode.OIDC))
    assert not hasattr(adapter, "issue_token")


# ------------------------------------------------- development adapter setup
def test_development_adapter_requires_a_signing_secret() -> None:
    """Constructed directly with an empty secret, it must refuse."""
    settings = _settings(auth_mode=AuthMode.OIDC)  # bypasses the Settings guard
    blank = settings.model_copy(update={"auth_mode": AuthMode.DEVELOPMENT})
    object.__setattr__(blank, "jwt_secret", type(settings.jwt_secret)(""))

    with pytest.raises(ValueError, match="JWT_SECRET"):
        DevelopmentIdentityAdapter(blank)


async def test_development_adapter_round_trips_a_token() -> None:
    adapter = DevelopmentIdentityAdapter(_settings())
    token = adapter.issue_token(
        subject="user-9",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.ANALYST, ScopeType.PLANT, "plant-7"),),
    )

    principal = await adapter.authenticate(token)

    assert principal.subject == "user-9"
    assert principal.tenant_id == TENANT_A
    assert principal.assignments == (
        RoleAssignment(Role.ANALYST, ScopeType.PLANT, "plant-7"),
    )


async def test_development_adapter_rejects_an_empty_token() -> None:
    adapter = DevelopmentIdentityAdapter(_settings())
    with pytest.raises(MissingTokenError):
        await adapter.authenticate("")


# ------------------------------------------------------------ claims mapping
def _claims(**raw: object) -> TokenClaims:
    payload = {"sub": "user-1", "exp": 1, "iat": 0, **raw}
    return TokenClaims(
        subject="user-1",
        issuer=None,
        audience=None,
        expires_at=1,
        issued_at=0,
        raw=payload,
    )


def test_claims_mapper_uses_configured_claim_names() -> None:
    """Provider claim names are configuration, not a hard-coded assumption."""
    mapper = ClaimsMapper(tenant_claim="org", roles_claim="entitlements")
    principal = mapper.to_principal(_claims(org=TENANT_A, entitlements=["ADMIN"]))

    assert principal.tenant_id == TENANT_A
    assert principal.roles == {Role.ADMIN}


def test_claims_mapper_rejects_a_missing_tenant() -> None:
    mapper = ClaimsMapper(tenant_claim="tenant_id", roles_claim="roles")
    with pytest.raises(InvalidTokenError):
        mapper.to_principal(_claims(roles=["ADMIN"]))


def test_claims_mapper_drops_malformed_role_entries() -> None:
    """Malformed entries must never widen access."""
    mapper = ClaimsMapper(tenant_claim="tenant_id", roles_claim="roles")
    principal = mapper.to_principal(
        _claims(
            tenant_id=TENANT_A,
            roles=[
                {"role": "PLANT_MANAGER", "scope_type": "PLANT", "scope_id": "plant-1"},
                {"role": "ADMIN", "scope_type": "GALAXY", "scope_id": "everything"},
                {"role": "ADMIN", "scope_type": "PLANT"},  # no scope_id
                {"role": "NOT_A_ROLE", "scope_type": "TENANT", "scope_id": TENANT_A},
                12345,
            ],
        )
    )
    assert principal.assignments == (
        RoleAssignment(Role.PLANT_MANAGER, ScopeType.PLANT, "plant-1"),
    )


def test_claims_mapper_tolerates_a_non_list_roles_claim() -> None:
    mapper = ClaimsMapper(tenant_claim="tenant_id", roles_claim="roles")
    principal = mapper.to_principal(_claims(tenant_id=TENANT_A, roles="ADMIN"))
    assert principal.assignments == ()


# ---------------------------------------------------------------- validator
async def test_validator_rejects_a_blank_token() -> None:
    validator = JwtValidator(StaticSecretKeyResolver(TEST_JWT_SECRET))
    with pytest.raises(MissingTokenError):
        await validator.validate("   ")


async def test_validator_reports_expiry_distinctly() -> None:
    """Callers need to tell "refresh me" from "this token is bad"."""
    import time

    import jwt as pyjwt

    now = int(time.time())
    token = pyjwt.encode(
        {"sub": "u", "iat": now - 100, "exp": now - 10},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    validator = JwtValidator(StaticSecretKeyResolver(TEST_JWT_SECRET))
    with pytest.raises(ExpiredTokenError):
        await validator.validate(token)


def test_static_resolver_pins_the_algorithm() -> None:
    """Algorithms come from the resolver, never from the token header."""
    resolver = StaticSecretKeyResolver(TEST_JWT_SECRET, "HS256")
    assert resolver.algorithms == ["HS256"]


def test_static_resolver_requires_a_secret() -> None:
    with pytest.raises(ValueError, match="signing secret"):
        StaticSecretKeyResolver("")
