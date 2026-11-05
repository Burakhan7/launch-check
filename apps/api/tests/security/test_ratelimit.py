from launchcheck.security.ratelimit import RateLimiter, hash_ip
from tests.conftest import FakeRedis


async def test_limit_enforced_per_ip_and_kind() -> None:
    redis = FakeRedis()
    limiter = RateLimiter(redis, salt="s", window_s=3600)
    results = [await limiter.hit("web", "1.2.3.4", limit=3) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[-1].retry_after_s == 3600
    assert (await limiter.hit("web", "5.6.7.8", limit=3)).allowed  # başka IP
    assert (await limiter.hit("apk", "1.2.3.4", limit=3)).allowed  # başka tür


async def test_ip_is_hashed_and_key_has_ttl() -> None:
    redis = FakeRedis()
    await RateLimiter(redis, salt="s").hit("web", "1.2.3.4", limit=10)
    (key,) = redis.counts
    assert "1.2.3.4" not in key
    assert key.endswith(hash_ip("1.2.3.4", "s"))
    assert redis.ttls[key] == 3600


def test_hash_depends_on_salt() -> None:
    assert hash_ip("1.2.3.4", "a") != hash_ip("1.2.3.4", "b")


async def test_fails_open_when_redis_down() -> None:
    assert (await RateLimiter(FakeRedis(fail=True), salt="s").hit("web", "1.2.3.4", 1)).allowed
