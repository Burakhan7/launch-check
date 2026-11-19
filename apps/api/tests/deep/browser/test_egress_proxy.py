"""Egress proxy (tarayıcısız): her istek SSRF kurallarından geçer, doğrulanan IP'ye bağlanır."""

import asyncio

import pytest

from launchcheck.deep.browser.egress_proxy import EgressProxy, Reason, parse_request
from launchcheck.deep.browser.launcher import chromium_args
from launchcheck.security.ssrf import UrlGuard
from tests.conftest import FakeResolver
from tests.deep.browser.servers import Canary, LocalHttpServer, Route

MB = 1024 * 1024


async def send(proxy_port: int, raw: bytes, then: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(raw)
    await writer.drain()
    if then:
        head = await reader.readuntil(b"\r\n\r\n")
        writer.write(then)
        await writer.drain()
        body = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        return head + body
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return data


def status(response: bytes) -> int:
    return int(response.split(b" ", 2)[1])


@pytest.fixture
def resolver() -> FakeResolver:
    return FakeResolver({"site.test": ["127.0.0.1"], "redis": ["172.18.0.5"]})


async def test_http_request_forwarded_in_origin_form(resolver: FakeResolver) -> None:
    async with LocalHttpServer({"/a": Route(b"merhaba")}) as server:
        allow = frozenset({("127.0.0.1", server.port)})
        async with EgressProxy(UrlGuard(resolver=resolver), 10 * MB, allow=allow) as proxy:
            raw = (
                f"GET http://site.test:{server.port}/a?x=1 HTTP/1.1\r\n"
                f"Host: site.test:{server.port}\r\nProxy-Connection: keep-alive\r\n"
                "Proxy-Authorization: Basic eDp5\r\n\r\n"
            ).encode()
            response = await send(proxy.port, raw)
    assert status(response) == 200 and response.endswith(b"merhaba")
    (head,) = server.requests
    assert head.startswith(b"GET /a?x=1 HTTP/1.1\r\n")
    assert b"Proxy-" not in head and b"Connection: close" in head
    assert proxy.stats.allowed == 1 and proxy.stats.bytes_in > 0


async def test_connect_tunnel_to_allowed_target(resolver: FakeResolver) -> None:
    async with LocalHttpServer({"/": Route(b"tunnel ok")}) as server:
        allow = frozenset({("127.0.0.1", server.port)})
        async with EgressProxy(UrlGuard(resolver=resolver), MB, allow=allow) as proxy:
            response = await send(
                proxy.port,
                f"CONNECT site.test:{server.port} HTTP/1.1\r\n\r\n".encode(),
                then=b"GET / HTTP/1.1\r\nHost: site.test\r\n\r\n",
            )
    assert response.startswith(b"HTTP/1.1 200 Connection Established")
    assert response.endswith(b"tunnel ok")


@pytest.mark.parametrize(
    ("request_line", "reason"),
    [
        ("CONNECT 169.254.169.254:443", Reason.PRIVATE_ADDRESS),  # bulut metadata
        ("GET http://169.254.169.254/latest/meta-data/", Reason.PRIVATE_ADDRESS),
        ("GET http://10.0.0.1/", Reason.PRIVATE_ADDRESS),
        ("CONNECT [::1]:443", Reason.PRIVATE_ADDRESS),  # IPv6 loopback
        ("CONNECT [::ffff:127.0.0.1]:443", Reason.PRIVATE_ADDRESS),
        ("GET http://2130706433/", Reason.PRIVATE_ADDRESS),  # 127.0.0.1 ondalık yazım
        ("CONNECT redis:6379", Reason.PORT),  # iç servis + standart dışı port
        ("CONNECT redis:443", Reason.PRIVATE_ADDRESS),
        ("CONNECT 93.184.216.34:22", Reason.PORT),
        ("GET ftp://example.com/", Reason.SCHEME),
        ("GET /relative", Reason.SCHEME),
    ],
)
async def test_blocked_targets(resolver: FakeResolver, request_line: str, reason: str) -> None:
    async with EgressProxy(UrlGuard(resolver=resolver), MB) as proxy:
        response = await send(proxy.port, f"{request_line} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    assert status(response) in (400, 403)
    assert [b.reason for b in proxy.stats.blocked] == [reason]
    assert proxy.stats.allowed == 0


async def test_loopback_not_in_allowlist_is_blocked(resolver: FakeResolver) -> None:
    """Allowlist (IP, port) çiftidir: aynı IP'nin başka portu (ör. yerel Redis) engellenir."""
    async with Canary() as canary, LocalHttpServer() as server:
        allow = frozenset({("127.0.0.1", server.port)})
        async with EgressProxy(UrlGuard(resolver=resolver), MB, allow=allow) as proxy:
            response = await send(
                proxy.port, f"CONNECT 127.0.0.1:{canary.port} HTTP/1.1\r\n\r\n".encode()
            )
    assert status(response) == 403 and canary.hits == 0


async def test_any_private_ip_in_answer_blocks(resolver: FakeResolver) -> None:
    resolver.mapping["mixed.test"] = ["93.184.216.34", "10.1.2.3"]
    async with EgressProxy(UrlGuard(resolver=resolver), MB) as proxy:
        response = await send(proxy.port, b"CONNECT mixed.test:443 HTTP/1.1\r\n\r\n")
    assert status(response) == 403
    assert proxy.stats.blocked[0].reason == Reason.PRIVATE_ADDRESS


async def test_dns_rebinding_connects_to_validated_ip_only() -> None:
    """Host bir kez çözülür ve o IP'ye bağlanılır; ikinci (kötü) yanıt hiç kullanılmaz."""
    answers = iter([["127.0.0.1"], ["10.0.0.1"]])
    calls: list[str] = []

    async def rebinding(host: str, port: int) -> list[str]:
        calls.append(host)
        return next(answers)

    async with LocalHttpServer({"/": Route(b"ilk yanit")}) as server:
        allow = frozenset({("127.0.0.1", server.port)})
        async with EgressProxy(UrlGuard(resolver=rebinding), MB, allow=allow) as proxy:
            raw = f"GET http://rebind.test:{server.port}/ HTTP/1.1\r\nHost: r\r\n\r\n".encode()
            response = await send(proxy.port, raw)
    assert response.endswith(b"ilk yanit")
    assert calls == ["rebind.test"]


async def test_rebinding_to_private_is_blocked() -> None:
    async def evil(host: str, port: int) -> list[str]:
        return ["192.168.1.1"]

    async with EgressProxy(UrlGuard(resolver=evil), MB) as proxy:
        response = await send(proxy.port, b"GET http://rebind.test/ HTTP/1.1\r\nHost: r\r\n\r\n")
    assert status(response) == 403


async def test_unresolvable_host() -> None:
    async def nx(host: str, port: int) -> list[str]:
        raise OSError("NXDOMAIN")

    async with EgressProxy(UrlGuard(resolver=nx), MB) as proxy:
        response = await send(proxy.port, b"CONNECT nope.test:443 HTTP/1.1\r\n\r\n")
    assert status(response) == 403
    assert proxy.stats.blocked[0].reason == Reason.UNRESOLVABLE


async def test_byte_budget_blocks_further_requests(resolver: FakeResolver) -> None:
    async with LocalHttpServer({"/big": Route(b"x" * 5000)}) as server:
        allow = frozenset({("127.0.0.1", server.port)})
        async with EgressProxy(UrlGuard(resolver=resolver), 1000, allow=allow) as proxy:
            raw = f"GET http://site.test:{server.port}/big HTTP/1.1\r\nHost: s\r\n\r\n".encode()
            first = await send(proxy.port, raw)
            second = await send(proxy.port, raw)
    assert len(first) < 5000  # bütçe dolunca bağlantı kesildi
    assert status(second) == 403
    assert proxy.stats.blocked[-1].reason == Reason.BUDGET


async def test_malformed_requests_rejected(resolver: FakeResolver) -> None:
    async with EgressProxy(UrlGuard(resolver=resolver), MB) as proxy:
        for raw in (
            b"garbage\r\n\r\n",
            b"CONNECT :443 HTTP/1.1\r\n\r\n",
            b"CONNECT a:99999 HTTP/1.1\r\n\r\n",
        ):
            assert status(await send(proxy.port, raw)) == 400
    assert all(b.reason == Reason.BAD_REQUEST for b in proxy.stats.blocked)


def test_parse_request_keeps_upgrade_for_websocket() -> None:
    request = parse_request(
        b"GET ws://site.test/socket HTTP/1.1\r\nHost: site.test\r\nUpgrade: websocket\r\n"
        b"Connection: Upgrade\r\n\r\n"
    )
    assert (request.host, request.port) == ("site.test", 80)
    assert b"Connection: Upgrade" in request.head and b"Connection: close" not in request.head


async def test_blocked_entries_have_no_path_or_query(resolver: FakeResolver) -> None:
    async with EgressProxy(UrlGuard(resolver=resolver), MB) as proxy:
        await send(proxy.port, b"GET http://10.0.0.1/secret?token=abc HTTP/1.1\r\nHost: x\r\n\r\n")
    assert "secret" not in repr(proxy.stats.blocked) and "abc" not in repr(proxy.stats.blocked)


def test_launch_flags_close_proxy_bypasses() -> None:
    args = chromium_args(41234)
    assert "--proxy-server=http://127.0.0.1:41234" in args
    assert "--proxy-bypass-list=<-loopback>" in args
    assert "--disable-quic" in args
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1" in args
