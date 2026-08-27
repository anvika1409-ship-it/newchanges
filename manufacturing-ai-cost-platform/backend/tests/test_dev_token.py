"""The development token command must work locally and refuse elsewhere.

It signs tokens that grant real access, so the guards matter more than the
happy path: it must not mint anything in production, and it must not mint
anything when the deployment is configured to trust an OIDC provider.
"""

from __future__ import annotations

import pytest

from app.core.config import AppEnv, AuthMode, Settings
from app.security.dev_token import DEFAULT_TENANT_ID, build_token
from app.security.identity import DevelopmentIdentityAdapter
from app.security.permissions import Permission, has_permission
from app.security.principal import Role, ScopeType


def _mint(settings: Settings, monkeypatch: pytest.MonkeyPatch, role: Role = Role.ADMIN) -> str:
    monkeypatch.setattr("app.security.dev_token.get_settings", lambda: settings)
    return build_token(
        role=role,
        subject=f"demo-{str(role).lower()}",
        tenant_id=DEFAULT_TENANT_ID,
        scope_type=ScopeType.TENANT,
        scope_id=None,
        ttl_seconds=None,
    )


@pytest.mark.asyncio
async def test_minted_token_authenticates_with_the_requested_role(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _mint(settings, monkeypatch, Role.AI_ENGINEER)

    principal = await DevelopmentIdentityAdapter(settings).authenticate(token)

    assert principal.subject == "demo-ai_engineer"
    assert principal.tenant_id == DEFAULT_TENANT_ID
    assert has_permission(principal, Permission.AI_EXECUTE)


@pytest.mark.asyncio
async def test_minted_viewer_token_does_not_grant_execution(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command must not quietly over-grant. A VIEWER token spends nothing."""
    token = _mint(settings, monkeypatch, Role.VIEWER)

    principal = await DevelopmentIdentityAdapter(settings).authenticate(token)

    assert not has_permission(principal, Permission.AI_EXECUTE)


def test_refuses_in_production(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    production = settings.model_copy(update={"app_env": AppEnv.PRODUCTION})

    with pytest.raises(SystemExit, match="APP_ENV=production"):
        _mint(production, monkeypatch)


def test_refuses_when_auth_mode_is_oidc(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under OIDC, tokens come from the provider. Signing our own would bypass it."""
    oidc = settings.model_copy(update={"auth_mode": AuthMode.OIDC})

    with pytest.raises(SystemExit, match="development"):
        _mint(oidc, monkeypatch)
