"""Translate an authorized scope into a SQL predicate.

``app.security.scope.AuthorizedScope`` is the tenant/plant/department constraint
resolved from the caller's role assignments. This module turns it into a WHERE
clause so unauthorized rows are never read, rather than being read and then
filtered out in Python — which would leak counts through pagination totals and
do work on data the caller may not see (SECURITY.md section 5).

The translation must agree with ``AuthorizedScope.covers``. Both express the
same rule — a disjunction of branches, each a conjunction of the levels it
constrains — and ``tests/test_cost_repository.py`` asserts that a row survives
this filter exactly when ``covers`` accepts it.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, or_
from sqlalchemy.orm import InstrumentedAttribute

from app.security.scope import AuthorizedScope


def authorized_scope_filter(
    scope: AuthorizedScope,
    *,
    tenant_column: InstrumentedAttribute[str | None],
    plant_column: InstrumentedAttribute[str | None],
    department_column: InstrumentedAttribute[str | None],
) -> ColumnElement[bool]:
    """Build the predicate for ``scope`` over a table's ownership columns.

    Args:
        scope: the resolved constraint. Never empty — ``AuthorizedScope``
            refuses to construct with no branches, so this cannot degrade into
            an unconstrained query.
        tenant_column: the table's ``tenant_id`` column.
        plant_column: the table's ``plant_id`` column.
        department_column: the table's ``department_id`` column.

    Returns:
        ``tenant_id = :tenant AND (branch OR branch OR ...)``.
    """
    branch_predicates: list[ColumnElement[bool]] = []

    for branch in scope.branches:
        conditions: list[ColumnElement[bool]] = []
        if branch.plant_id is not None:
            conditions.append(plant_column == branch.plant_id)
        if branch.department_id is not None:
            conditions.append(department_column == branch.department_id)

        if not conditions:
            # A tenant-wide branch subsumes every other branch, so the whole
            # disjunction collapses to the tenant predicate alone.
            return tenant_column == scope.tenant_id

        predicate = conditions[0]
        for extra in conditions[1:]:
            predicate = predicate & extra
        branch_predicates.append(predicate)

    return (tenant_column == scope.tenant_id) & or_(*branch_predicates)
