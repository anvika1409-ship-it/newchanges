"""JWT validation.

SECURITY.md section 3 requires JWT validation and short-lived access tokens.
This module holds the validation itself, independent of where the signing key
comes from: the development adapter resolves a local symmetric key, an OIDC
adapter resolves a public key from the provider's JWKS. Both run exactly the
same checks.

Nothing here is permissive by default. Signature, expiry, not-before, issuer and
audience are all verified, and ``sub``/``exp``/``iat`` are required to be
present. A token that omits a claim is rejected rather than defaulted.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import jwt

from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)


class TokenError(AppError):
    """Base for token failures. Always surfaces as 401."""

    status_code = 401
    code = "invalid_token"
    message = "Authentication credentials are not valid"


class InvalidTokenError(TokenError):
    code = "invalid_token"
    message = "Authentication credentials are not valid"


class ExpiredTokenError(TokenError):
    code = "token_expired"
    message = "Authentication credentials have expired"


class MissingTokenError(TokenError):
    code = "unauthorized"
    message = "Authentication required"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Validated claims.

    ``raw`` keeps the full payload so a deployment-specific claims mapper can
    read provider claims this platform does not model.
    """

    subject: str
    issuer: str | None
    audience: str | None
    expires_at: int
    issued_at: int
    raw: dict[str, Any]

    def claim(self, name: str, default: Any = None) -> Any:
        return self.raw.get(name, default)


class SigningKeyResolver(ABC):
    """Supplies the key material used to verify a token's signature."""

    @property
    @abstractmethod
    def algorithms(self) -> list[str]:
        """Algorithms this resolver will accept.

        Returned explicitly so ``jwt.decode`` is never called with a
        token-controlled algorithm, which is how algorithm-confusion attacks
        (including ``alg: none``) succeed.
        """

    @abstractmethod
    async def resolve(self, key_id: str | None) -> Any:
        """Return key material for the given ``kid``."""


class StaticSecretKeyResolver(SigningKeyResolver):
    """Single symmetric key held in configuration.

    Development only. A shared symmetric secret means any holder can mint
    tokens, so this must never back a production deployment.
    """

    def __init__(self, secret: str, algorithm: str = "HS256") -> None:
        if not secret:
            raise ValueError("A signing secret is required")
        self._secret = secret
        self._algorithm = algorithm

    @property
    def algorithms(self) -> list[str]:
        return [self._algorithm]

    async def resolve(self, key_id: str | None) -> Any:  # noqa: ARG002 - single key
        return self._secret


class JwtValidator:
    """Verifies a bearer token and returns its claims."""

    def __init__(
        self,
        resolver: SigningKeyResolver,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        leeway_seconds: float = 0.0,
        required_claims: tuple[str, ...] = ("sub", "exp", "iat"),
    ) -> None:
        self._resolver = resolver
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_seconds
        self._required = list(required_claims)

    async def validate(self, token: str) -> TokenClaims:
        if not token or not token.strip():
            raise MissingTokenError()

        # Read the header only to select a key. Nothing from an unverified
        # token is trusted for anything else.
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            logger.info("token_header_unreadable")
            raise InvalidTokenError() from exc

        key = await self._resolver.resolve(header.get("kid"))

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=self._resolver.algorithms,
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={
                    "require": self._required,
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": self._audience is not None,
                    "verify_iss": self._issuer is not None,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            # Distinguished from a generic failure so a client can tell a stale
            # token from a bad one and refresh. It is still a 401.
            logger.info("token_expired")
            raise ExpiredTokenError() from exc
        except jwt.PyJWTError as exc:
            # Everything else — bad signature, wrong issuer or audience, missing
            # required claim, malformed token — is one indistinguishable
            # failure. Saying which check failed helps an attacker tune.
            logger.info("token_rejected", extra={"reason": type(exc).__name__})
            raise InvalidTokenError() from exc

        audience = payload.get("aud")
        if isinstance(audience, list):
            audience = audience[0] if audience else None

        return TokenClaims(
            subject=str(payload["sub"]),
            issuer=payload.get("iss"),
            audience=audience,
            expires_at=int(payload["exp"]),
            issued_at=int(payload["iat"]),
            raw=payload,
        )
