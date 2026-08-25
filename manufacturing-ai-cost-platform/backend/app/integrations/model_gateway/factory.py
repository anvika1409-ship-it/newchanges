"""Model gateway selection.

The provider is configuration, never a code branch scattered through business
logic (AI_DEVELOPMENT_RULES.md section 6).
"""

from __future__ import annotations

from app.core.config import ModelGatewayProvider, Settings
from app.core.logging import get_logger
from app.integrations.model_gateway.base import ModelGatewayInterface
from app.integrations.model_gateway.genailab import GenAILabAdapter
from app.integrations.model_gateway.mock import MockModelGateway

logger = get_logger(__name__)


def build_model_gateway(settings: Settings) -> ModelGatewayInterface:
    """Return the gateway implementation named by configuration."""
    match settings.model_gateway_provider:
        case ModelGatewayProvider.GENAILAB:
            gateway: ModelGatewayInterface = GenAILabAdapter(settings)
        case ModelGatewayProvider.MOCK:
            gateway = MockModelGateway()
        case _:  # pragma: no cover - StrEnum makes this unreachable
            raise ValueError(
                f"Unsupported model gateway provider: {settings.model_gateway_provider}"
            )

    logger.info("model_gateway_selected", extra={"provider": gateway.provider_name})
    return gateway
