"""Mint a development bearer token for local work and demonstrations.

Every protected endpoint requires a bearer token, and there is no login route:
tokens come from an identity provider in any real deployment. That leaves a
local operator with no way to call the API or drive the UI at all, which is
what this command exists to solve.

    python -m app.security.dev_token --role ADMIN
    python -m app.security.dev_token --role AI_ENGINEER --subject demo-engineer

Deliberately a CLI and not an HTTP endpoint. A route that mints tokens is a
privilege-escalation path the moment it is reachable, and SECURITY.md places
token issuance with the identity provider. This runs only where someone already
has the signing secret in their environment, and it refuses to run in
production, where ``Settings`` rejects the development adapter anyway.

The token it prints grants real access for its lifetime. Treat it as a
credential: do not paste it into a ticket, a chat, or a commit.
"""

from __future__ import annotations

import argparse
import sys

from app.core.config import AppEnv, AuthMode, get_settings
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType

#: The tenant the demo dataset is seeded under.
DEFAULT_TENANT_ID = "tenant-acme-manufacturing"


def build_token(
    *,
    role: Role,
    subject: str,
    tenant_id: str,
    scope_type: ScopeType,
    scope_id: str | None,
    ttl_seconds: int | None,
) -> str:
    settings = get_settings()

    if settings.app_env is AppEnv.PRODUCTION:
        raise SystemExit(
            "Refusing to mint a development token while APP_ENV=production. "
            "Obtain a token from the configured identity provider."
        )
    if settings.auth_mode is not AuthMode.DEVELOPMENT:
        raise SystemExit(
            f"AUTH_MODE is '{settings.auth_mode}', not 'development'. "
            "This command only signs tokens for the development adapter."
        )

    adapter = DevelopmentIdentityAdapter(settings)
    return adapter.issue_token(
        subject=subject,
        tenant_id=tenant_id,
        assignments=(
            RoleAssignment(role, scope_type, scope_id or tenant_id),
        ),
        expires_in_seconds=ttl_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.security.dev_token",
        description="Mint a development bearer token. Never available in production.",
    )
    parser.add_argument(
        "--role",
        default="ADMIN",
        choices=[str(r) for r in Role],
        help="Role granted by the token (default: ADMIN).",
    )
    parser.add_argument("--subject", default=None, help="Token subject (default: demo-<role>).")
    parser.add_argument(
        "--tenant", default=DEFAULT_TENANT_ID, help="Tenant the token is scoped to."
    )
    parser.add_argument(
        "--scope-type",
        default="TENANT",
        choices=[str(s) for s in ScopeType],
        help="Scope the role is assigned at (default: TENANT).",
    )
    parser.add_argument(
        "--scope-id",
        default=None,
        help="Scope identifier. Defaults to the tenant id.",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=None,
        help="Lifetime in seconds. Defaults to the configured access-token TTL.",
    )
    args = parser.parse_args(argv)

    role = Role(args.role)
    subject = args.subject or f"demo-{str(role).lower()}"

    print(
        build_token(
            role=role,
            subject=subject,
            tenant_id=args.tenant,
            scope_type=ScopeType(args.scope_type),
            scope_id=args.scope_id,
            ttl_seconds=args.ttl,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
