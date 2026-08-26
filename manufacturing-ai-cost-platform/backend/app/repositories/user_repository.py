"""User and role repositories.

Access to users, roles and user_roles tables. All user queries are
tenant-scoped to enforce tenant isolation (SECURITY.md section 5).
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.control_plane import Role, User, UserRole
from app.repositories.base import AsyncRepository


class UserRepository(AsyncRepository[User]):
    """Read/write access to the ``users`` table.

    Every list query requires a tenant_id so cross-tenant reads are not
    possible through the repository layer.
    """

    async def get_by_id(self, user_id: str, tenant_id: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str, tenant_id: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.username == username, User.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self, tenant_id: str, limit: int = 100, offset: int = 0
    ) -> list[User]:
        result = await self.session.execute(
            select(User)
            .where(User.tenant_id == tenant_id)
            .order_by(User.username)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def add(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user


class RoleRepository(AsyncRepository[Role]):
    """Read/write access to the ``roles`` table."""

    async def get_by_id(self, role_id: str) -> Role | None:
        return await self.session.get(Role, role_id)

    async def get_by_name(self, name: str) -> Role | None:
        result = await self.session.execute(select(Role).where(Role.name == name))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Role]:
        result = await self.session.execute(select(Role).order_by(Role.name))
        return list(result.scalars().all())

    async def add(self, role: Role) -> Role:
        self.session.add(role)
        await self.session.flush()
        return role


class UserRoleRepository(AsyncRepository[UserRole]):
    """Read/write access to the ``user_roles`` table."""

    async def list_by_user(self, user_id: str) -> list[UserRole]:
        result = await self.session.execute(
            select(UserRole).where(UserRole.user_id == user_id)
        )
        return list(result.scalars().all())

    async def add(self, user_role: UserRole) -> UserRole:
        self.session.add(user_role)
        await self.session.flush()
        return user_role

    async def delete(
        self, user_id: str, role_id: str, scope_type: str, scope_id: str
    ) -> bool:
        obj = await self.session.get(
            UserRole, {"user_id": user_id, "role_id": role_id,
                       "scope_type": scope_type, "scope_id": scope_id}
        )
        if obj is None:
            return False
        await self.session.delete(obj)
        await self.session.flush()
        return True
