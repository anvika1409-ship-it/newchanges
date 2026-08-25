"""Initial security hooks.

SECURITY.md section 3 permits a development authentication adapter for the MVP,
provided the structure allows an enterprise OIDC implementation later.

Two deliberate choices here:

* The development adapter is explicit about being a development adapter and is
  refused when ``APP_ENV=production`` (enforced in ``Settings``).
* The OIDC path is **not** implemented. It raises rather than performing a
  token check that looks real but validates nothing. A convincing fake is worse
  than an obvious gap.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import AuthMode, Settings
from app.core.context import set_tenant_id, set_user_id
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.logging import get_logger
from app.security.principal import Principal, Role, RoleAssignment, ScopeType

logger = get_logger(__name__)

# auto_error=False so a missing header produces our contract-shaped 401 rather
# than FastAPI's default body.
bearer_scheme = HTTPBearer(auto_error=False)


def get_settings_dependency(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_current_principal(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> Principal:
    """Resolve the authenticated principal for a protected endpoint."""
    settings: Settings = request.app.state.settings

    match settings.auth_mode:
        case AuthMode.DEVELOPMENT:
            principal = _development_principal(settings)
        case AuthMode.OIDC:
            # Deliberately unimplemented. See module docstring.
            raise NotImplementedError(
                "AUTH_MODE=oidc requires an enterprise OIDC adapter, which is not "
                "implemented in this scaffold. Implement JWT validation against "
                "the configured issuer before enabling it."
            )
        case _:  # pragma: no cover - StrEnum makes this unreachable
            raise UnauthorizedError()

    if credentials is not None and not credentials.credentials:
        raise UnauthorizedError()

    # Correlation only. Authorization decisions use the Principal, never these.
    set_tenant_id(principal.tenant_id)
    set_user_id(principal.subject)
    return principal


def _development_principal(settings: Settings) -> Principal:
    """Build the local development principal from configuration."""
    assignments: list[RoleAssignment] = []
    for raw_role in settings.dev_principal_roles or ["VIEWER"]:
        try:
            role = Role(raw_role.strip().upper())
        except ValueError:
            logger.warning("dev_principal_unknown_role", extra={"role": raw_role})
            continue
        assignments.append(
            RoleAssignment(
                role=role,
                scope_type=ScopeType.TENANT,
                scope_id=settings.dev_principal_tenant_id,
            )
        )

    return Principal(
        subject=settings.dev_principal_subject,
        tenant_id=settings.dev_principal_tenant_id,
        assignments=tuple(assignments),
    )


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_roles(
    *roles: Role,
) -> Callable[[Principal], Awaitable[Principal]]:
    """Endpoint-level authorization guard.

    Resource/object-level checks are a separate concern and are evaluated in the
    service layer against the principal's scoped assignments
    (SECURITY.md section 4). This guard is the endpoint half only.
    """

    async def _guard(principal: CurrentPrincipal) -> Principal:
        if not principal.has_role(*roles):
            logger.warning(
                "authorization_denied",
                extra={"required_roles": [str(r) for r in roles]},
            )
            raise ForbiddenError()
        return principal

    return _guard
