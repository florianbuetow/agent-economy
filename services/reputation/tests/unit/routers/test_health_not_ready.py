"""The health endpoint's not-ready error code follows the service's snake_case
convention (`service_not_ready`), matching every other 503 in this service.

Found during the WP-12 spec sweep: `routers/health.py` was the one remaining
UPPERCASE error code in the codebase (no test pinned it).
"""

import pytest
from httpx import AsyncClient

from reputation_service.core.state import get_app_state


@pytest.mark.unit
async def test_health_not_ready_uses_snake_case_code(client: AsyncClient) -> None:
    state = get_app_state()
    saved_store = state.feedback_store
    state.feedback_store = None
    try:
        response = await client.get("/health")
        assert response.status_code == 503
        assert response.json()["error"] == "service_not_ready"
    finally:
        state.feedback_store = saved_store
