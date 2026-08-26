"""Authentication, authorization and principal handling.

Module map (SECURITY.md sections 3, 4 and 5):

``principal``
    ``Principal``, ``Role``, ``ScopeType``, ``RoleAssignment``,
    ``ResourceScope``. The authenticated caller and the ownership of a record.
``tokens``
    JWT validation, independent of where the signing key comes from.
``identity``
    ``IdentityAdapter`` port, the development adapter, and the OIDC seam.
``permissions``
    ``Permission`` and the role grant table. The single place role-to-operation
    policy is stated.
``authorization``
    Endpoint-level and resource-level checks for a record already loaded.
``scope``
    ``AuthorizedScope``: the tenant/plant/department constraint a repository
    applies to a collection query.
``dependencies``
    FastAPI wiring: ``CurrentPrincipal``, ``RequirePermission``,
    ``RequireScope``, ``RequireRoles``.
``route_protection``
    Startup audit that no API route is left unauthenticated.
``events``
    Security event names for monitoring.

Import from the submodule rather than from this package, so the dependency a
module actually has is visible at its import site.
"""
