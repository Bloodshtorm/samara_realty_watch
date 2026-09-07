from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from app.web import app, db_session


@pytest.mark.parametrize("available", [True, False])
async def test_health_does_not_expose_database_errors(available):
    session = AsyncMock()
    if not available:
        session.execute.side_effect = OperationalError("secret-db-path", {}, Exception("secret"))

    async def override():
        yield session

    app.dependency_overrides[db_session] = override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/healthz")
        assert response.status_code == (200 if available else 503)
        assert response.json() == {"status": "ok" if available else "unavailable"}
        assert "secret" not in response.text
    finally:
        app.dependency_overrides.clear()
