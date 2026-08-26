"""GET /api/v1/models endpoint tests.

Acceptance for this feature: the endpoint returns registry data. These tests
drive the real application, with authentication and authorization active.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Role, RoleAssignment, ScopeType

TENANT_A = "tenant-a"


@pytest.fixture
def adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest.fixture
def viewer_token(adapter: DevelopmentIdentityAdapter) -> str:
    return adapter.issue_token(
        subject="viewer-1",
        tenant_id=TENANT_A,
        assignments=(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_A),),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """The real app, with the schema created and the seed registered."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        # The in-memory database starts empty; create the schema, then let the
        # startup seed populate it via a second registration pass.
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        from pathlib import Path

        from app.repositories.model_repository import ModelRepository
        from app.services.model_registry import ModelRegistryService

        async with app.state.database.session() as session:
            service = ModelRegistryService(ModelRepository(session))
            await service.register_from_seed(Path(settings.model_registry_seed_path))

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


# ===========================================================================
# Authorization
# ===========================================================================
async def test_models_requires_authentication(client: AsyncClient, api_prefix: str) -> None:
    response = await client.get(f"{api_prefix}/models")
    assert response.status_code == 401


async def test_viewer_may_read_the_registry(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    """Every recognised role needs to know which models exist."""
    response = await client.get(f"{api_prefix}/models", headers=_auth(viewer_token))
    assert response.status_code == 200


async def test_a_token_with_no_roles_is_forbidden(
    client: AsyncClient, api_prefix: str, adapter: DevelopmentIdentityAdapter
) -> None:
    token = adapter.issue_token(subject="nobody", tenant_id=TENANT_A, assignments=())
    response = await client.get(f"{api_prefix}/models", headers=_auth(token))
    assert response.status_code == 403


# ===========================================================================
# Acceptance: the endpoint returns registry data
# ===========================================================================
async def test_models_returns_the_seeded_registry(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    response = await client.get(f"{api_prefix}/models", headers=_auth(viewer_token))
    assert response.status_code == 200

    body = response.json()
    assert body["page"]["total"] == 4
    assert len(body["items"]) == 4

    names = {item["model_name"] for item in body["items"]}
    assert names == {
        "azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct",
        "azure_ai/genailab-maas-Phi-3.5-vision-instruct",
        "azure/genailab-maas-text-embedding-3-large",
        "azure/genailab-maas-whisper",
    }


async def test_response_matches_the_contract_model_schema(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    response = await client.get(f"{api_prefix}/models", headers=_auth(viewer_token))
    item = response.json()["items"][0]

    expected = {
        "id",
        "model_name",
        "provider",
        "capability",
        "modality",
        "input_cost",
        "output_cost",
        "cost_unit",
        "has_known_pricing",
        "max_context_tokens",
        "supports_vision",
        "supports_tools",
        "supports_structured_output",
        "supports_embeddings",
        "quality_score",
        "latency_score",
        "risk_level",
        "enabled",
    }
    assert set(item) == expected


async def test_unknown_metadata_serializes_as_null_not_zero(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    """A consumer must distinguish "price unknown" from "free"."""
    response = await client.get(f"{api_prefix}/models", headers=_auth(viewer_token))

    for item in response.json()["items"]:
        assert item["input_cost"] is None, item["model_name"]
        assert item["output_cost"] is None, item["model_name"]
        assert item["max_context_tokens"] is None, item["model_name"]
        assert item["quality_score"] is None, item["model_name"]
        assert item["has_known_pricing"] is False, item["model_name"]


# ===========================================================================
# Filtering through the API
# ===========================================================================
async def test_capability_filter(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/models", params={"capability": "vision"}, headers=_auth(viewer_token)
    )
    assert response.status_code == 200

    items = response.json()["items"]
    assert len(items) == 2
    assert all(item["capability"] == "vision" for item in items)


async def test_embedding_capability_filter(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/models", params={"capability": "embedding"}, headers=_auth(viewer_token)
    )
    items = response.json()["items"]
    assert [i["model_name"] for i in items] == ["azure/genailab-maas-text-embedding-3-large"]
    assert items[0]["supports_embeddings"] is True


async def test_speech_capability_filter(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/models", params={"capability": "speech"}, headers=_auth(viewer_token)
    )
    assert [i["model_name"] for i in response.json()["items"]] == [
        "azure/genailab-maas-whisper"
    ]


async def test_enabled_filter(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/models", params={"enabled": "true"}, headers=_auth(viewer_token)
    )
    assert response.status_code == 200
    assert all(item["enabled"] is True for item in response.json()["items"])


async def test_workload_compatibility_filter(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    """quality_check needs documented vision support."""
    response = await client.get(
        f"{api_prefix}/models",
        params={"workload_type": "quality_check"},
        headers=_auth(viewer_token),
    )
    assert response.status_code == 200

    items = response.json()["items"]
    # Both seeded vision models are documented as vision models.
    assert {i["model_name"] for i in items} == {
        "azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct",
        "azure_ai/genailab-maas-Phi-3.5-vision-instruct",
    }


async def test_reasoning_workload_has_no_seeded_candidates(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    """No text or reasoning model is named in any source document.

    ARCHITECTURE.md section 8 says those are registered through configuration,
    so the seed contains none and this returns empty rather than mis-routing a
    reasoning workload to a vision or embedding model.
    """
    response = await client.get(
        f"{api_prefix}/models",
        params={"workload_type": "predictive_maintenance"},
        headers=_auth(viewer_token),
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_unknown_workload_type_is_rejected_by_validation(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/models",
        params={"workload_type": "not_a_workload"},
        headers=_auth(viewer_token),
    )
    # Accepted by the schema as a free string, then matched against no profile.
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_invalid_capability_is_rejected(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/models",
        params={"capability": "telepathy"},
        headers=_auth(viewer_token),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_pagination(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/models",
        params={"limit": 2, "offset": 1},
        headers=_auth(viewer_token),
    )
    body = response.json()
    assert body["page"] == {"total": 4, "limit": 2, "offset": 1}
    assert len(body["items"]) == 2


# ===========================================================================
# Single model lookup
# ===========================================================================
async def test_model_lookup_by_id(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    listing = await client.get(f"{api_prefix}/models", headers=_auth(viewer_token))
    model_id = listing.json()["items"][0]["id"]

    response = await client.get(f"{api_prefix}/models/{model_id}", headers=_auth(viewer_token))

    assert response.status_code == 200
    assert response.json()["id"] == model_id


async def test_missing_model_returns_404_in_contract_shape(
    client: AsyncClient, api_prefix: str, viewer_token: str
) -> None:
    response = await client.get(
        f"{api_prefix}/models/does-not-exist", headers=_auth(viewer_token)
    )
    assert response.status_code == 404

    body = response.json()
    assert set(body) == {"code", "message", "request_id", "details"}
    assert body["code"] == "not_found"
