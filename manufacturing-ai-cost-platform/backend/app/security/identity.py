"""Identity adapters.

SECURITY.md section 3 permits a development authentication adapter for the MVP
provided the structure allows an enterprise OIDC implementation later. That is
the split here:

* ``IdentityAdapter`` — the port the application depends on.
* ``DevelopmentIdentityAdapter`` — locally signed HS256 tokens. The validation
  is real; only the key source is local.
* ``OidcIdentityAdapter`` — the seam for a real provider. It is **not**
  implemented and raises. A stub that returned a principal without verifying
  anything would be far more dangerous than an obvious gap.

Claim mapping is deliberately configurable. Tenant and role claims are not
standardised by OIDC — every provider names them differently — so the platform
must not hard-code one vendor's shape. ``ClaimsMapper`` is the single place a
deployment adapts its provider's claims onto this model.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from typing import Any

import jwt

from app.core.config import AuthMode, Settings
from app.core.logging import get_logger
from app.security.principal import Principal, Role, RoleAssignment, ScopeType
from app.security.tokens import (
    InvalidTokenError,
    JwtValidator,
    StaticSecretKeyResolver,
    TokenClaims,
)

logger = get_logger(__name__)


class ClaimsMapper:
    """Maps validated token claims onto a ``Principal``.

    The roles claim accepts either shape:

    * structured, preserving scope::

          [{"role": "PLANT_MANAGER", "scope_type": "PLANT", "scope_id": "plant-a"}]

    * plain strings, treated as TENANT scope over the token's own tenant::

          ["ADMIN", "ANALYST"]

    Unknown role names and malformed entries are dropped with a log line rather
    than failing the request: an unrecognised role must never widen access, and
    the caller still has whatever valid roles remain.
    """

    def __init__(self, *, tenant_claim: str, roles_claim: str) -> None:
        self._tenant_claim = tenant_claim
        self._roles_claim = roles_claim

    def to_principal(self, claims: TokenClaims) -> Principal:
        tenant_id = claims.claim(self._tenant_claim)
        if not isinstance(tenant_id, str) or not tenant_id:
            # Without a tenant there is no isolation boundary to enforce, so the
            # token is unusable regardless of its signature.
            logger.info("token_missing_tenant_claim", extra={"claim": self._tenant_claim})
            raise InvalidTokenError()

        raw_roles = claims.claim(self._roles_claim) or []
        if not isinstance(raw_roles, list):
            logger.info("token_roles_claim_not_a_list", extra={"claim": self._roles_claim})
            raw_roles = []

        assignments: list[RoleAssignment] = []
        for entry in raw_roles:
            assignment = self._parse_assignment(entry, tenant_id)
            if assignment is not None:
                assignments.append(assignment)

        return Principal(
            subject=claims.subject,
            tenant_id=tenant_id,
            assignments=tuple(assignments),
        )

    def _parse_assignment(self, entry: Any, tenant_id: str) -> RoleAssignment | None:
        if isinstance(entry, str):
            role = self._parse_role(entry)
            if role is None:
                return None
            return RoleAssignment(role=role, scope_type=ScopeType.TENANT, scope_id=tenant_id)

        if isinstance(entry, dict):
            role = self._parse_role(entry.get("role"))
            if role is None:
                return None
            try:
                scope_type = ScopeType(str(entry.get("scope_type", "")).upper())
            except ValueError:
                logger.info("token_role_unknown_scope_type")
                return None
            scope_id = entry.get("scope_id")
            if not isinstance(scope_id, str) or not scope_id:
                logger.info("token_role_missing_scope_id")
                return None
            return RoleAssignment(role=role, scope_type=scope_type, scope_id=scope_id)

        logger.info("token_role_entry_unrecognised")
        return None

    @staticmethod
    def _parse_role(value: Any) -> Role | None:
        if not isinstance(value, str):
            return None
        try:
            return Role(value.strip().upper())
        except ValueError:
            logger.info("token_unknown_role_ignored")
            return None


class IdentityAdapter(ABC):
    """The authentication port the application depends on."""

    mode: AuthMode

    @abstractmethod
    async def authenticate(self, token: str) -> Principal:
        """Validate a bearer token and resolve the caller.

        Raises:
            TokenError: for any credential that is missing, malformed, expired
                or otherwise unacceptable.
        """


class DevelopmentIdentityAdapter(IdentityAdapter):
    """Locally signed tokens for development and testing.

    Signature, expiry, issuer and audience are all genuinely verified. The only
    development-grade part is the symmetric key: anyone holding it can mint a
    token, which is why ``Settings`` refuses this adapter in production.
    """

    mode = AuthMode.DEVELOPMENT

    def __init__(self, settings: Settings) -> None:
        secret = settings.jwt_secret.get_secret_value()
        if not secret:
            raise ValueError(
                "JWT_SECRET must be set when AUTH_MODE=development. "
                "Generate a random value; do not reuse one across environments."
            )
        self._settings = settings
        self._algorithm = settings.jwt_algorithm
        self._secret = secret
        self._validator = JwtValidator(
            StaticSecretKeyResolver(secret, settings.jwt_algorithm),
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            leeway_seconds=settings.jwt_leeway_seconds,
        )
        self._mapper = ClaimsMapper(
            tenant_claim=settings.jwt_tenant_claim,
            roles_claim=settings.jwt_roles_claim,
        )

    async def authenticate(self, token: str) -> Principal:
        claims = await self._validator.validate(token)
        return self._mapper.to_principal(claims)

    def issue_token(
        self,
        *,
        subject: str,
        tenant_id: str,
        assignments: tuple[RoleAssignment, ...] = (),
        expires_in_seconds: int | None = None,
        issued_at: int | None = None,
    ) -> str:
        """Mint a development token.

        Present so local development and tests do not need a real identity
        provider. It exists only on this adapter — there is no equivalent on the
        OIDC path, where tokens come from the provider.
        """
        now = issued_at if issued_at is not None else int(time.time())
        ttl = (
            expires_in_seconds
            if expires_in_seconds is not None
            else self._settings.jwt_access_token_ttl_seconds
        )

        payload: dict[str, Any] = {
            "sub": subject,
            "iat": now,
            "nbf": now,
            "exp": now + ttl,
            "jti": str(uuid.uuid4()),
            self._settings.jwt_tenant_claim: tenant_id,
            self._settings.jwt_roles_claim: [
                {
                    "role": str(a.role),
                    "scope_type": str(a.scope_type),
                    "scope_id": a.scope_id,
                }
                for a in assignments
            ],
        }
        if self._settings.jwt_issuer:
            payload["iss"] = self._settings.jwt_issuer
        if self._settings.jwt_audience:
            payload["aud"] = self._settings.jwt_audience

        return jwt.encode(payload, self._secret, algorithm=self._algorithm)


class OidcIdentityAdapter(IdentityAdapter):
    """Enterprise OIDC/OAuth2 seam.

    Intentionally unimplemented. Wiring this up requires decisions this
    repository must not invent: the issuer, the JWKS endpoint, the audience, the
    signing algorithms to accept, and which provider claims carry tenant and
    role. Those belong to the deployment.

    To implement, supply a ``SigningKeyResolver`` that fetches and caches the
    provider's JWKS by ``kid`` and returns only asymmetric algorithms, then
    reuse ``JwtValidator`` and ``ClaimsMapper`` unchanged. No other part of the
    application changes.
    """

    mode = AuthMode.OIDC

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def authenticate(self, token: str) -> Principal:
        raise NotImplementedError(
            "AUTH_MODE=oidc is not implemented. Provide a JWKS-backed "
            "SigningKeyResolver and configure the issuer, audience and claim "
            "mapping for your provider. See OidcIdentityAdapter's docstring."
        )


def build_identity_adapter(settings: Settings) -> IdentityAdapter:
    """Select the adapter named by configuration."""
    match settings.auth_mode:
        case AuthMode.DEVELOPMENT:
            adapter: IdentityAdapter = DevelopmentIdentityAdapter(settings)
        case AuthMode.OIDC:
            adapter = OidcIdentityAdapter(settings)
        case _:  # pragma: no cover - StrEnum makes this unreachable
            raise ValueError(f"Unsupported auth mode: {settings.auth_mode}")

    logger.info("identity_adapter_selected", extra={"auth_mode": str(adapter.mode)})
    return adapter
