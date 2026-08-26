"""Authentication tests.

Covers the required cases: unauthenticated request, invalid token, expired
token, and authorized access.

Every assertion checks that access was *refused*. None of these tests may be
made to pass by relaxing a check — if one fails, the defect is in the
authorization code, not in the expectation.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, TEST_JWT_SECRET
from tests.security_app import create_security_test_app

TENANT_A = "tenant-a"


@pytest.fixture
def adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_security_test_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


def _admin_token(adapter: DevelopmentIdentityAdapter, **kwargs: object) -> str:
    return adapter.issue_token(
        subject="user-1",
        tenant_id=TENANT_A,
        assignments=(
            RoleAssignment(Role.ADMIN, ScopeType.TENANT, TENANT_A),
        ),
        **kwargs,  # type: ignore[arg-type]
    )


# ------------------------------------------------------- unauthenticated
async def test_request_without_a_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/whoami")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


async def test_empty_bearer_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/whoami", headers={"Authorization": "Bearer "})
    assert response.status_code == 401


async def test_non_bearer_scheme_is_rejected(client: AsyncClient) -> None:
    """Basic auth must not be accepted where a bearer token is required."""
    response = await client.get("/whoami", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 401


# -------------------------------------------------------- invalid token
async def test_garbage_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/whoami", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


async def test_token_signed_with_the_wrong_key_is_rejected(client: AsyncClient) -> None:
    """The signature is genuinely verified."""
    now = int(time.time())
    forged = jwt.encode(
        {
            "sub": "attacker",
            "iat": now,
            "exp": now + 900,
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "tenant_id": TENANT_A,
            "roles": ["ADMIN"],
        },
        "a-different-signing-key-padded-to-thirty-two-bytes",
        algorithm="HS256",
    )
    response = await client.get("/whoami", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


async def test_unsigned_alg_none_token_is_rejected(client: AsyncClient) -> None:
    """`alg: none` must never bypass verification."""
    now = int(time.time())
    unsigned = jwt.encode(
        {
            "sub": "attacker",
            "iat": now,
            "exp": now + 900,
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "tenant_id": TENANT_A,
            "roles": ["ADMIN"],
        },
        key="",
        algorithm="none",
    )
    response = await client.get("/whoami", headers={"Authorization": f"Bearer {unsigned}"})
    assert response.status_code == 401


async def test_token_from_a_different_issuer_is_rejected(client: AsyncClient) -> None:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-1",
            "iat": now,
            "exp": now + 900,
            "iss": "some-other-issuer",
            "aud": TEST_AUDIENCE,
            "tenant_id": TENANT_A,
            "roles": ["ADMIN"],
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    response = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_token_for_a_different_audience_is_rejected(client: AsyncClient) -> None:
    """A token minted for another service must not be replayable here."""
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-1",
            "iat": now,
            "exp": now + 900,
            "iss": TEST_ISSUER,
            "aud": "some-other-api",
            "tenant_id": TENANT_A,
            "roles": ["ADMIN"],
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    response = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_token_without_a_tenant_claim_is_rejected(client: AsyncClient) -> None:
    """No tenant means no isolation boundary, so the token is unusable."""
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-1",
            "iat": now,
            "exp": now + 900,
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "roles": ["ADMIN"],
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    response = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


# --------------------------------------------------------- expired token
async def test_expired_token_is_rejected(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    issued = int(time.time()) - 3600
    token = _admin_token(adapter, issued_at=issued, expires_in_seconds=60)

    response = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    # Distinguished from a bad token so a client knows to refresh.
    assert response.json()["code"] == "token_expired"


async def test_token_expiring_one_second_ago_is_rejected(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    """No accidental grace period: leeway defaults to zero."""
    issued = int(time.time()) - 61
    token = _admin_token(adapter, issued_at=issued, expires_in_seconds=60)

    response = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


async def test_not_yet_valid_token_is_rejected(client: AsyncClient) -> None:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-1",
            "iat": now,
            "nbf": now + 600,
            "exp": now + 1200,
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "tenant_id": TENANT_A,
            "roles": ["ADMIN"],
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    response = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


# ------------------------------------------------------ authorized access
async def test_valid_token_is_accepted(
    client: AsyncClient, adapter: DevelopmentIdentityAdapter
) -> None:
    token = _admin_token(adapter)
    response = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "user-1"
    assert body["tenant_id"] == TENANT_A
    assert body["roles"] == ["ADMIN"]


async def test_plain_string_roles_claim_is_mapped_to_tenant_scope(
    client: AsyncClient,
) -> None:
    """A provider emitting flat role strings still yields scoped assignments."""
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-1",
            "iat": now,
            "exp": now + 900,
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "tenant_id": TENANT_A,
            "roles": ["ANALYST", "VIEWER"],
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    response = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["roles"] == ["ANALYST", "VIEWER"]


async def test_unknown_role_in_claim_is_ignored_not_granted(
    client: AsyncClient,
) -> None:
    """An unrecognised role must never widen access."""
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-1",
            "iat": now,
            "exp": now + 900,
            "iss": TEST_ISSUER,
            "aud": TEST_AUDIENCE,
            "tenant_id": TENANT_A,
            "roles": ["SUPER_ADMIN", "VIEWER"],
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    response = await client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["roles"] == ["VIEWER"]
