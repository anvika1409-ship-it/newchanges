"""Startup audit: every API route must be authenticated.

SECURITY.md section 18 requires "authorization on every protected endpoint".
Relying on each route author to remember is how endpoints ship unguarded — the
failure is silent, looks like a working feature, and is found by whoever probes
it first.

This module makes the omission impossible to miss: at application startup, every
route registered under the API prefix is inspected for a dependency on
``get_current_principal``. A route without one, and not listed in
``PUBLIC_OPERATIONS``, aborts startup. Deny by default, checked by the machine.

The allowlist is a literal set rather than a decorator or a setting, on purpose:

* A decorator marking a route public can be added by the same edit that forgets
  the guard, which defeats the check.
* A setting could be switched off in an environment, and an authorization
  control that can be disabled by configuration is not a control.

Adding a public endpoint therefore requires editing this file, which is exactly
the friction the decision deserves.

Note what this does *not* verify. Presence of authentication is a structural
property and can be checked automatically; whether a route uses the right
permission, and whether it performs the resource-level check that
SECURITY.md section 4 also requires, cannot be. Those remain review items —
``RequireRoles``/``RequirePermission`` alone is half of the requirement.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.core.logging import get_logger
from app.security.dependencies import get_current_principal
from app.security.events import SecurityEvent, record_security_event

logger = get_logger(__name__)

# Paths, relative to the API prefix, that API_CONTRACT.yaml declares with no
# `security:` requirement. These are the only two such operations in the
# contract; both are infrastructure probes returning no tenant data.
PUBLIC_OPERATIONS: frozenset[str] = frozenset({"/health", "/ready"})


class UnprotectedRouteError(RuntimeError):
    """Raised at startup when a route has no authentication dependency.

    A startup failure, not a warning. A warning in a log nobody reads would let
    the unguarded endpoint serve traffic, which is the outcome this check
    exists to prevent.
    """


def _depends_on_authentication(dependant: object) -> bool:
    """Does this route resolve ``get_current_principal``, at any depth?

    Walks the whole dependency tree, so a guard reached through
    ``RequireRoles``/``RequirePermission`` — or through any future wrapper built
    on ``CurrentPrincipal`` — counts.
    """
    if dependant is None:
        return False
    if getattr(dependant, "call", None) is get_current_principal:
        return True
    return any(
        _depends_on_authentication(sub)
        for sub in getattr(dependant, "dependencies", ())
    )


def unprotected_routes(app: FastAPI, *, api_prefix: str) -> list[str]:
    """Routes under ``api_prefix`` that neither authenticate nor are public.

    Returns:
        ``"METHOD /path"`` strings, sorted, for the failure message and for
        tests that assert the check actually catches an omission.
    """
    offenders: set[str] = set()

    for route in app.routes:
        if not isinstance(route, APIRoute):
            # Mounts, static files and the docs routes are not API operations.
            continue
        if not route.path.startswith(api_prefix):
            continue
        if route.path[len(api_prefix) :] in PUBLIC_OPERATIONS:
            continue
        if _depends_on_authentication(route.dependant):
            continue
        for method in sorted(route.methods or {"GET"}):
            offenders.add(f"{method} {route.path}")

    return sorted(offenders)


def verify_route_protection(app: FastAPI, *, api_prefix: str) -> None:
    """Fail startup if any API route is unauthenticated.

    Raises:
        UnprotectedRouteError: listing every offending operation, so one
            startup attempt reports all of them rather than one per run.
    """
    offenders = unprotected_routes(app, api_prefix=api_prefix)
    if offenders:
        for offender in offenders:
            record_security_event(
                SecurityEvent.ROUTE_UNPROTECTED,
                reason="no_authentication_dependency",
                operation=offender,
            )
        raise UnprotectedRouteError(
            "These API routes have no authentication dependency: "
            + ", ".join(offenders)
            + ". Depend on CurrentPrincipal (directly, or through RequirePermission "
            "/ RequireRoles), or add the path to "
            "app.security.route_protection.PUBLIC_OPERATIONS if the contract "
            "declares it without a security requirement."
        )

    logger.info(
        "route_protection_verified",
        extra={"public_operations": sorted(PUBLIC_OPERATIONS)},
    )
