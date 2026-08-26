"""FastAPI security dependencies.

Wires the identity adapter and authorization rules into request handling.

Endpoint-level and resource-level checks are separate on purpose
(SECURITY.md section 4). ``RequirePermission`` guards the operation; the handler
then calls ``authorize_resource_permission`` once it knows which record is being
touched, or takes an ``AuthorizedScope`` from ``RequireScope`` and hands it to a
repository. A route that only declares ``RequirePermission`` has completed half
the check.

Which guard to use:

``CurrentPrincipal``
    Authentication only. For an operation every authenticated caller may
    perform. Rare.
``RequirePermission(Permission.X)``
    Endpoint-level RBAC. The default choice.
``RequireScope(Permission.X)``
    Endpoint-level RBAC **and** the tenant/plant/department constraint for a
    collection query, resolved from the contract's ``plant_id`` and
    ``department_id`` query parameters.

``RequireRoles`` remains for the cases where an operation is tied to specific
roles rather than to a named permission. New routes should prefer a permission:
role lists at call sites drift out of step with each other.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.context import set_tenant_id, set_user_id
from app.core.logging import get_logger
from app.security.authorization import require_roles as _require_roles
from app.security.events import SecurityEvent, record_security_event
from app.security.identity import IdentityAdapter
from app.security.permissions import Permission
from app.security.permissions import require_permission as _require_permission
from app.security.principal import Principal, Role
from app.security.scope import AuthorizedScope, resolve_authorized_scope
from app.security.tokens import MissingTokenError, TokenError

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
        record_security_event(
            SecurityEvent.AUTHENTICATION_FAILED,
            reason="missing_credentials",
            http_path=request.url.path,
        )
        raise MissingTokenError()

    if credentials.scheme.lower() != "bearer":
        record_security_event(
            SecurityEvent.AUTHENTICATION_FAILED,
            reason="unsupported_scheme",
            http_path=request.url.path,
        )
        raise MissingTokenError()

    adapter = get_identity_adapter(request)
    try:
        principal = await adapter.authenticate(credentials.credentials)
    except TokenError as exc:
        # Re-raised unchanged. Caught only so SECURITY.md section 20 gets one
        # event name for every authentication failure, whatever rejected it.
        # `exc.code` distinguishes an expired token from an invalid one; the
        # token itself is never logged.
        record_security_event(
            SecurityEvent.AUTHENTICATION_FAILED,
            reason=exc.code,
            http_path=request.url.path,
        )
        raise

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


def RequirePermission(  # noqa: N802 - reads as a dependency, not a function
    permission: Permission,
) -> Callable[[Principal], Awaitable[Principal]]:
    """Endpoint-level authorization guard, stated as a permission.

    Usage::

        @router.get("/budgets", dependencies=[Depends(RequirePermission(Permission.BUDGET_READ))])

    or, when the handler needs the principal::

        principal: Annotated[Principal, Depends(RequirePermission(Permission.BUDGET_READ))]

    The roles that grant the permission live in
    ``app.security.permissions.ROLE_PERMISSIONS`` and are not restated here.

    This does not authorize a specific record. Use ``RequireScope`` for a
    collection query, or call ``authorize_resource_permission`` in the handler
    once the record's tenant, plant and department are known.
    """

    async def _guard(principal: CurrentPrincipal) -> Principal:
        _require_permission(principal, permission)
        return principal

    return _guard


def RequireScope(  # noqa: N802 - reads as a dependency, not a function
    permission: Permission,
) -> Callable[..., Awaitable[AuthorizedScope]]:
    """Endpoint RBAC plus the tenant/plant/department constraint for a query.

    Usage::

        scope: Annotated[AuthorizedScope, Depends(RequireScope(Permission.COST_READ))]

    Reads the ``plant_id`` and ``department_id`` query parameters declared in
    API_CONTRACT.yaml and treats them as requests to *narrow*: a filter the
    caller's assignments cannot reach is a 403, not a silently ignored
    parameter. Tenant is always taken from the authenticated principal and is
    never a request parameter.

    Declare this only on operations whose contract entry defines those
    parameters — adding them elsewhere would put query parameters in the OpenAPI
    schema that the contract does not declare.

    The returned scope is a query constraint, not a decision. The repository
    must apply it; holding it and running an unconstrained query authorizes
    nothing.
    """

    async def _resolve(
        principal: CurrentPrincipal,
        plant_id: Annotated[str | None, Query()] = None,
        department_id: Annotated[str | None, Query()] = None,
    ) -> AuthorizedScope:
        return resolve_authorized_scope(
            principal,
            permission,
            requested_plant_id=plant_id,
            requested_department_id=department_id,
        )

    return _resolve
