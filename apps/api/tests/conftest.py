from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from launchcheck.db.models import Base
from launchcheck.db.session import get_session
from launchcheck.main import app
from launchcheck.security.ratelimit import RateLimiter
from launchcheck.security.ssrf import UrlGuard

PUBLIC_IP = "93.184.216.34"


class FakeQueue:
    def __init__(self, fail: bool = False) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail = fail

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any:
        if self.fail:
            raise ConnectionError("redis yok")
        self.jobs.append((function, args, kwargs))
        return None

    async def close(self) -> None:
        return None


class FakeResolver:
    """Host → IP listesi. Tanımsız host'lar herkese açık bir IP'ye çözülür."""

    def __init__(self, mapping: dict[str, list[str]] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        value = self.mapping.get(host, [PUBLIC_IP])
        if not value:
            raise OSError("NXDOMAIN")
        return value


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.ops: list[tuple[Any, ...]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self.ops.append(("incr", key))

    def expire(self, key: str, seconds: int, nx: bool = False) -> None:
        self.ops.append(("expire", key, seconds, nx))

    def ttl(self, key: str) -> None:
        self.ops.append(("ttl", key))

    async def execute(self) -> list[Any]:
        out: list[Any] = []
        for op in self.ops:
            if op[0] == "incr":
                self.redis.counts[op[1]] = self.redis.counts.get(op[1], 0) + 1
                out.append(self.redis.counts[op[1]])
            elif op[0] == "expire":
                if not (op[3] and op[1] in self.redis.ttls):
                    self.redis.ttls[op[1]] = op[2]
                out.append(True)
            else:
                out.append(self.redis.ttls.get(op[1], -1))
        return out


class FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.values: dict[str, str] = {}
        self.fail = fail

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        if self.fail:
            raise ConnectionError("redis yok")
        return FakePipeline(self)

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise ConnectionError("redis yok")
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        if self.fail:
            raise ConnectionError("redis yok")
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True


@pytest.fixture
def resolver() -> FakeResolver:
    return FakeResolver()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn: Any, _: Any) -> None:
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def client(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: FakeQueue,
    resolver: FakeResolver,
    fake_redis: FakeRedis,
) -> AsyncIterator[AsyncClient]:
    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.state.queue = queue
    app.state.url_guard = UrlGuard(resolver=resolver)
    app.state.rate_limiter = RateLimiter(fake_redis, salt="test")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
