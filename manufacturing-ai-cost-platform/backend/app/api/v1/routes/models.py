"""Model registry endpoints.

Implements ``GET /models`` from API_CONTRACT.yaml. The response shape is the
contract's ``ModelList``; no field is added or renamed here.

The registry is platform-global: ``models`` carries no tenant column
(DATABASE_SCHEMA.md section 11), because a model is infrastructure rather than
tenant data. Authorization is therefore endpoint-level only — any authenticated
principal holding a recognised role may read it. There is no resource-level
tenant check to make, and inventing one would be misleading.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.v1.schemas.model_registry import ModelListResponse, ModelResponse, PageInfo
from app.core.errors import NotFoundError
from app.db.models.registry import Capability, Modality
from app.repositories.model_repository import ModelRepository
from app.security.dependencies import RequireRoles
from app.security.principal import Role
from app.services.model_registry import ModelRegistryService

router = APIRouter(tags=["Models"])

#: Reading the registry is broad: every recognised role needs to know which
#: models exist. Mutating it is a separate, narrower operation and is not
#: implemented here.
READ_ROLES = (
    Role.ADMIN,
    Role.FINOPS_MANAGER,
    Role.AI_ENGINEER,
    Role.PLANT_MANAGER,
    Role.ANALYST,
    Role.VIEWER,
)


async def get_model_service(request: Request) -> AsyncIterator[ModelRegistryService]:
    """Build the service for this request, bound to a session."""
    database = request.app.state.database
    async with database.session() as session:
        yield ModelRegistryService(ModelRepository(session))


ModelService = Annotated[ModelRegistryService, Depends(get_model_service)]


@router.get(
    "/models",
    summary="List registered models",
    response_model=ModelListResponse,
    dependencies=[Depends(RequireRoles(*READ_ROLES))],
)
async def list_models(
    service: ModelService,
    capability: Annotated[Capability | None, Query()] = None,
    modality: Annotated[Modality | None, Query()] = None,
    enabled: Annotated[
        bool | None,
        Query(
            description=(
                "Filter by enabled status. Omit to return both enabled and "
                "disabled models."
            )
        ),
    ] = None,
    workload_type: Annotated[
        str | None,
        Query(
            description=(
                "Return only models compatible with this workload type. "
                "Implies enabled=true. An unrecognised value returns no models."
            )
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ModelListResponse:
    """List the model registry.

    Cost, context, quality and latency fields are null wherever the value is
    unknown. A null is not zero and must not be rendered as one
    (AI_DEVELOPMENT_RULES.md sections 41 and 42).
    """
    if workload_type is not None:
        # Compatibility filtering is inherently execution-oriented, so it only
        # ever returns enabled models.
        entries = await service.find_for_workload(
            workload_type, modality=modality, limit=limit
        )
        window = entries[offset : offset + limit]
        return ModelListResponse(
            items=[ModelResponse.from_entry(e) for e in window],
            page=PageInfo(total=len(entries), limit=limit, offset=offset),
        )

    page = await service.list_models(
        capability=capability,
        modality=modality,
        enabled=enabled,
        limit=limit,
        offset=offset,
    )
    return ModelListResponse(
        items=[ModelResponse.from_entry(e) for e in page.items],
        page=PageInfo(total=page.total, limit=page.limit, offset=page.offset),
    )


@router.get(
    "/models/{id}",
    summary="Get a registered model",
    response_model=ModelResponse,
    dependencies=[Depends(RequireRoles(*READ_ROLES))],
)
async def get_model(
    id: str,  # noqa: A002 - the contract names this path parameter "id"
    service: ModelService,
) -> ModelResponse:
    """Look up one model by its registry id."""
    entry = await service.get(id)
    if entry is None:
        raise NotFoundError()
    return ModelResponse.from_entry(entry)
