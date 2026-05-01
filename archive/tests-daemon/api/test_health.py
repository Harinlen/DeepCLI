"""Tests for health check endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from daemon.app import create_app


@pytest.fixture
def app():
    """Create a test app instance."""
    return create_app()


class TestHealthEndpoint:
    """Tests for GET /health."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self, app) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        # started_at is the daemon's startup epoch — included so clients
        # can detect restarts.
        assert "started_at" in body
        assert isinstance(body["started_at"], (int, float))
