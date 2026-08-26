"""FastAPI security dependencies.

Wires the identity adapter and authorization rules into request handling.

Endpoint-level and resource-level checks are separate on purpose
(SECURITY.md section 4). ``RequireRoles`` guards the operation; the handler then
calls ``authorize_resource`` once it knows which record is being touched. A
route that only declares ``RequireRoles`` has completed half the check.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.context import set_tenant_id, set_user_id
from app.core.logging import get_logger
from app.security.authorization import require_roles as _require_roles
from app.security.identity import IdentityAdapter
from app.security.principal import Principal, Role
from app.security.tokens import MissingTokenError

logger = get_logger(__name__)

# auto_error=False so a missing or malformed header reaches our handlers and
# returns the contract's Error shape rather than FastAPI's default body.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="bearerAuth")


def get_settings_dependency(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_identity_adapter(request: Request) -> IdentityAdapter:
    adapter: IdentityAdapter = request.app.state.identity_adapter
    return adapter


async def get_current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> Principal:
    """Authenticate the caller.

    Every protected endpoint depends on this, directly or through a role guard.
    """
    if credentials is None or not credentials.credentials:
        logger.info("authentication_missing_credentials")
        raise MissingTokenError()

    if credentials.scheme.lower() != "bearer":
        logger.info("authentication_wrong_scheme")
        raise MissingTokenError()

    adapter = get_identity_adapter(request)
    principal = await adapter.authenticate(credentials.credentials)

    # Correlation for logs and telemetry only. Authorization decisions read the
    # Principal itself, never these context values.
    set_tenant_id(principal.tenant_id)
    set_user_id(principal.subject)
    request.state.principal = principal

    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def RequireRoles(*roles: Role) -> Callable[[Principal], Awaitable[Principal]]:  # noqa: N802
    """Endpoint-level authorization guard.

    Usage::

        @router.get("/budgets", dependencies=[Depends(RequireRoles(Role.ADMIN))])

    or, when the handler needs the principal::

        principal: Annotated[Principal, Depends(RequireRoles(Role.ADMIN))]

    This does not authorize a specific record. Call ``authorize_resource`` in
    the handler once the resource's tenant, plant and department are known.
    """
    if not roles:
        raise ValueError("RequireRoles needs at least one role")

    async def _guard(principal: CurrentPrincipal) -> Principal:
        _require_roles(principal, *roles)
        return principal

    return _guard
