"""SSRF testleri. Dış ağa çıkılmaz: DNS sahte resolver, HTTP respx ile taklit edilir."""

import ipaddress
from typing import Any

import httpcore
import httpx
import pytest
import respx

from launchcheck.collectors.fetcher import FetchError, SafeFetcher
from launchcheck.config import Settings
from launchcheck.security.ssrf import (
    GuardedNetworkBackend,
    GuardedTransport,
    UrlGuard,
    is_public_ip,
    parse_ip,
)
from launchcheck.security.url import TargetValidationError
from tests.conftest import PUBLIC_IP, FakeResolver

BLOCKED = [
    "127.0.0.1",
    "127.255.255.254",
    "10.0.0.1",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.169.254",  # bulut metadata
    "169.254.0.1",
    "100.64.0.1",  # CGNAT
    "100.127.255.254",
    "0.0.0.0",
    "224.0.0.1",
    "239.255.255.250",
    "240.0.0.1",
    "255.255.255.255",
    "192.0.2.1",
    "198.18.0.1",
    "::1",
    "::",
    "fe80::1",
    "fc00::1",
    "fd12:3456::1",
    "ff02::1",
    "::ffff:127.0.0.1",  # IPv4-mapped loopback
    "::ffff:169.254.169.254",
    "::ffff:10.0.0.1",
    "2002:7f00:1::",  # 6to4 içinde 127.0.0.1
    "2002:a9fe:a9fe::",  # 6to4 içinde 169.254.169.254
    "64:ff9b::7f00:1",  # NAT64 içinde 127.0.0.1
    "64:ff9b::a9fe:a9fe",  # NAT64 içinde metadata
    "fd00:ec2::254",  # AWS IPv6 metadata
    "2001:db8::1",
]
ALLOWED = ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"]


def test_nat64_blocked_even_for_public_ipv4() -> None:
    """Python 64:ff9b::/96'yı reserved sayar; sunucumuz DNS64 kullanmadığı için kabul edilebilir."""
    assert not is_public_ip(ipaddress.ip_address("64:ff9b::808:808"))


@pytest.mark.parametrize("ip", BLOCKED)
def test_blocked_ips(ip: str) -> None:
    assert not is_public_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ALLOWED)
def test_public_ips(ip: str) -> None:
    assert is_public_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://10.0.0.5/",
        "http://100.64.1.1/",
        "http://[fe80::1]/",
    ],
)
async def test_literal_private_ip_rejected(url: str) -> None:
    resolver = FakeResolver()
    with pytest.raises(TargetValidationError) as exc:
        await UrlGuard(resolver).check_url(url)
    assert exc.value.code == "url_not_public"
    assert resolver.calls == []  # literal IP için DNS'e gidilmez


@pytest.mark.parametrize(
    "host",
    ["2130706433", "0x7f000001", "127.1", "0177.0.0.1", "0x7f.0.0.1", "10.1", "0xa9fea9fe"],
)
async def test_obfuscated_ipv4_rejected(host: str) -> None:
    """Ondalık/onaltılık/sekizlik IP yazımları literal IP sayılır; DNS'e hiç gidilmez."""
    resolver = FakeResolver()
    with pytest.raises(TargetValidationError) as exc:
        await UrlGuard(resolver).check_url(f"http://{host}/")
    assert exc.value.code == "url_not_public"
    assert resolver.calls == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2130706433", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("127.1", "127.0.0.1"),
        ("0177.0.0.01", "127.0.0.1"),
        ("10.1.257", "10.1.1.1"),
        ("0xa9fea9fe", "169.254.169.254"),
        ("8.8.8.8", "8.8.8.8"),
    ],
)
def test_parse_inet_aton_forms(text: str, expected: str) -> None:
    assert str(parse_ip(text)) == expected


@pytest.mark.parametrize("text", ["example.com", "1.2.3.4.5", "256.1.1.1", "08.1.1.1", "1..1", ""])
def test_parse_ip_rejects_non_ips(text: str) -> None:
    assert parse_ip(text) is None


async def test_hostname_resolving_to_private_rejected() -> None:
    guard = UrlGuard(FakeResolver({"internal.example": ["10.1.2.3"]}))
    with pytest.raises(TargetValidationError) as exc:
        await guard.check_url("https://internal.example/")
    assert exc.value.code == "url_not_public"


async def test_any_private_ip_in_answer_rejected() -> None:
    guard = UrlGuard(FakeResolver({"mixed.example": [PUBLIC_IP, "127.0.0.1"]}))
    with pytest.raises(TargetValidationError):
        await guard.check_url("https://mixed.example/")


async def test_unresolvable() -> None:
    guard = UrlGuard(FakeResolver({"nope.example": []}))
    with pytest.raises(TargetValidationError) as exc:
        await guard.check_url("https://nope.example/")
    assert exc.value.code == "url_unresolvable"


async def test_public_host_allowed_and_query_kept() -> None:
    url = await UrlGuard(FakeResolver()).check_url("https://example.com/a.js?v=1#x")
    assert url == "https://example.com/a.js?v=1"


# --- Bağlantı katmanı (DNS rebinding) ---


class FakeStream(httpcore.AsyncNetworkStream):
    def __init__(self, peer: str) -> None:
        self.peer = peer

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:  # noqa: ASYNC109
        return b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:  # noqa: ASYNC109
        return None

    async def aclose(self) -> None:
        return None

    def get_extra_info(self, info: str) -> Any:
        return (self.peer, 443) if info == "server_addr" else None


class FakeInnerBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, peer: str | None = None) -> None:
        self.connected: list[str] = []
        self.peer = peer

    async def connect_tcp(self, host: str, port: int, **kwargs: Any) -> httpcore.AsyncNetworkStream:
        self.connected.append(host)
        return FakeStream(self.peer or host)

    async def sleep(self, seconds: float) -> None:
        return None


class RebindingResolver:
    """İlk sorguda herkese açık, sonrakilerde iç IP döner (klasik DNS rebinding)."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls += 1
        return [PUBLIC_IP] if self.calls == 1 else ["127.0.0.1"]


async def test_backend_connects_to_validated_ip_not_hostname() -> None:
    inner = FakeInnerBackend()
    backend = GuardedNetworkBackend(UrlGuard(FakeResolver()), inner)
    await backend.connect_tcp("example.com", 443)
    assert inner.connected == [PUBLIC_IP]


async def test_dns_rebinding_blocked_at_connect() -> None:
    guard = UrlGuard(RebindingResolver())
    await guard.check_url("https://rebind.example/")  # ön kontrol geçer
    inner = FakeInnerBackend()
    with pytest.raises(httpcore.ConnectError, match="url_not_public"):
        await GuardedNetworkBackend(guard, inner).connect_tcp("rebind.example", 443)
    assert inner.connected == []  # iç IP'ye hiç bağlanılmadı


async def test_peer_ip_verified_after_connect() -> None:
    backend = GuardedNetworkBackend(UrlGuard(FakeResolver()), FakeInnerBackend(peer="10.0.0.1"))
    with pytest.raises(httpcore.ConnectError, match="url_not_public"):
        await backend.connect_tcp("example.com", 443)


async def test_backend_rejects_non_standard_port() -> None:
    backend = GuardedNetworkBackend(UrlGuard(FakeResolver()), FakeInnerBackend())
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("example.com", 6379)


def test_transport_uses_guarded_backend() -> None:
    """httpx iç yapısı varsayımı. Bu test kırılırsa GuardedTransport güncellenmeli."""
    transport = GuardedTransport(UrlGuard(FakeResolver()))
    assert isinstance(transport._pool._network_backend, GuardedNetworkBackend)  # type: ignore[attr-defined]


async def test_real_transport_blocks_rebinding_end_to_end() -> None:
    """respx olmadan gerçek transport: ön kontrol geçer, bağlantı anında engellenir."""
    fetcher = SafeFetcher(Settings(), UrlGuard(RebindingResolver()))
    try:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch("http://rebind.example/", 1000)
        assert exc.value.code == "url_not_public"
    finally:
        await fetcher.aclose()


# --- Redirect'ler ---


def make_fetcher(resolver: FakeResolver | None = None, **overrides: Any) -> SafeFetcher:
    return SafeFetcher(Settings(**overrides), UrlGuard(resolver or FakeResolver()))


@respx.mock
@pytest.mark.parametrize(
    ("location", "code"),
    [
        ("http://127.0.0.1/admin", "url_not_public"),
        ("http://169.254.169.254/latest/meta-data/", "url_not_public"),
        ("http://[::1]/", "url_not_public"),
        ("http://internal.example/", "url_not_public"),
        ("http://example.com:6379/", "url_port_not_allowed"),
        ("gopher://example.com/", "url_scheme_not_allowed"),
    ],
)
async def test_redirect_to_forbidden_target_blocked(location: str, code: str) -> None:
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(302, headers={"location": location})
    )
    fetcher = make_fetcher(FakeResolver({"internal.example": ["192.168.0.10"]}))
    with pytest.raises(FetchError) as exc:
        await fetcher.fetch("https://example.com/", 1000)
    assert exc.value.code == code
    await fetcher.aclose()


@respx.mock
async def test_too_many_redirects() -> None:
    for i in range(10):
        respx.get(f"https://example.com/{i}").mock(
            return_value=httpx.Response(301, headers={"location": f"/{i + 1}"})
        )
    fetcher = make_fetcher()
    with pytest.raises(FetchError) as exc:
        await fetcher.fetch("https://example.com/0", 1000)
    assert exc.value.code == "too_many_redirects"
    await fetcher.aclose()


@respx.mock
async def test_redirect_followed_within_limit() -> None:
    respx.get("http://example.com/").mock(
        return_value=httpx.Response(301, headers={"location": "https://www.example.com/"})
    )
    respx.get("https://www.example.com/").mock(return_value=httpx.Response(200, text="ok"))
    fetcher = make_fetcher()
    res = await fetcher.fetch("http://example.com/", 1000)
    assert res.status == 200
    assert res.final_url == "https://www.example.com/"
    assert res.redirects == ["https://www.example.com/"]
    await fetcher.aclose()


@respx.mock
async def test_response_size_limit_and_total_budget() -> None:
    respx.get("https://example.com/big").mock(return_value=httpx.Response(200, content=b"a" * 5000))
    fetcher = make_fetcher(max_total_bytes=3000)
    first = await fetcher.fetch("https://example.com/big", 2000)
    assert (len(first.body), first.truncated) == (2000, True)
    second = await fetcher.fetch("https://example.com/big", 2000)
    assert (len(second.body), second.truncated) == (1000, True)  # toplam bütçe 3000
    assert fetcher.budget_remaining == 0
    await fetcher.aclose()


@respx.mock
async def test_user_agent_header() -> None:
    route = respx.get("https://example.com/").mock(return_value=httpx.Response(200))
    fetcher = make_fetcher()
    await fetcher.fetch("https://example.com/", 100)
    headers = route.calls[0].request.headers
    assert headers["user-agent"].startswith("LaunchCheckBot/1.0")
    assert headers["accept"].startswith("text/html")
    await fetcher.aclose()


@respx.mock
async def test_connection_error_is_site_unreachable() -> None:
    respx.get("https://example.com/").mock(side_effect=httpx.ConnectTimeout("t"))
    fetcher = make_fetcher()
    with pytest.raises(FetchError) as exc:
        await fetcher.fetch("https://example.com/", 100)
    assert exc.value.code == "site_unreachable"
    await fetcher.aclose()
