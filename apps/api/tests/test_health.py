from httpx import ASGITransport, AsyncClient

from launchcheck.main import app
from launchcheck.worker.tasks import hello


async def test_health() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_hello_job() -> None:
    assert await hello({}, "launch") == "hello launch"
