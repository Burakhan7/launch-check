"""SSRF koruması (AGENTS.md §8).

Katmanlar:
1. `UrlGuard.check_url`: şema/port + DNS çözümü; çözülen TÜM IP'ler herkese açık olmalı.
2. `GuardedNetworkBackend`: httpcore'un TCP bağlantısını açtığı noktada host'u yeniden çözer,
   doğrular ve doğrulanan IP'ye bağlanır. Böylece kontrol edilen IP = bağlanılan IP olur
   (DNS rebinding'e karşı). Bağlantı sonrası peer IP'si de tekrar kontrol edilir.
   TLS SNI/sertifika doğrulaması httpcore tarafından orijinal host adıyla yapılır.
3. Redirect'ler elle takip edilir; her adımda 1. ve 2. tekrar uygulanır (`SafeFetcher`).
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import urldefrag

import httpcore
import httpx

from launchcheck.security.url import ALLOWED_PORTS, TargetValidationError, normalize_target_url

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

# ipaddress.is_global bunların çoğunu zaten yakalar; açık liste savunma derinliği içindir.
BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local, 169.254.169.254 bulut metadata
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved + broadcast
        "::/128",
        "::1/128",
        "fc00::/7",  # unique local
        "fe80::/10",  # link-local
        "fec0::/10",  # site-local (eski)
        "ff00::/8",  # multicast
        "100::/64",  # discard
        "2001:db8::/32",  # dokümantasyon
        "fd00:ec2::254/128",  # AWS IPv6 metadata
    )
)
NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


class BlockedAddressError(TargetValidationError):
    def __init__(self) -> None:
        super().__init__("url_not_public")


class UnresolvableHostError(TargetValidationError):
    def __init__(self) -> None:
        super().__init__("url_unresolvable")


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv6 içine gömülü IPv4 (::ffff:a.b.c.d, 6to4, NAT64, Teredo istemcisi)."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if ip in NAT64_PREFIX:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_public_ip(ip: IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(ip)
        if embedded is not None and not is_public_ip(embedded):
            return False
    if any(ip in net for net in BLOCKED_NETWORKS if net.version == ip.version):
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


_INET_ATON_PART = re.compile(r"^(0x[0-9a-f]+|0[0-7]*|[1-9][0-9]*)$", re.IGNORECASE)
# inet_aton biçimleri: a | a.b | a.b.c | a.b.c.d; son parça kalan baytları doldurur
_INET_ATON_LAST_BITS = {1: 32, 2: 24, 3: 16, 4: 8}


def _parse_inet_aton(text: str) -> ipaddress.IPv4Address | None:
    """`2130706433`, `0x7f000001`, `127.1`, `0177.0.0.1` gibi eski IPv4 yazımları.
    Bazı işletim sistemleri bunları IP olarak çözer, bazıları çözmez; platformdan bağımsız
    olmak için kendimiz çözüp literal IP gibi değerlendiririz."""
    parts = text.split(".")
    if not 1 <= len(parts) <= 4 or not all(_INET_ATON_PART.match(p) for p in parts):
        return None
    values = [
        int(p, 0) if not (len(p) > 1 and p[0] == "0" and p[1] not in "xX") else int(p, 8)
        for p in parts
    ]
    *head, last = values
    if any(v > 0xFF for v in head) or last >= 1 << _INET_ATON_LAST_BITS[len(parts)]:
        return None
    number = 0
    for v in head:
        number = (number << 8) | v
    number = (number << _INET_ATON_LAST_BITS[len(parts)]) | last
    return ipaddress.IPv4Address(number)


def parse_ip(text: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(text.split("%", 1)[0])  # IPv6 zone id'yi at
    except ValueError:
        return _parse_inet_aton(text)


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class UrlGuard:
    def __init__(self, resolver: Resolver = system_resolver, timeout_s: float = 5.0) -> None:
        self._resolver = resolver
        self._timeout_s = timeout_s

    async def resolve(self, host: str, port: int) -> list[IPAddress]:
        """Host'u IP'lere çözer (literal ve eski IPv4 yazımları dahil). IP kontrolü YAPMAZ;
        çağıran `is_public_ip` ile tüm IP'leri kontrol etmelidir."""
        literal = parse_ip(host.strip("[]"))
        if literal is not None:
            return [literal]
        try:
            async with asyncio.timeout(self._timeout_s):
                raw = await self._resolver(host, port)
        except (OSError, TimeoutError, UnicodeError) as exc:
            raise UnresolvableHostError() from exc
        ips = [ip for ip in (parse_ip(r) for r in raw) if ip is not None]
        if not ips:
            raise UnresolvableHostError()
        return ips

    async def resolve_public(self, host: str, port: int) -> list[IPAddress]:
        """Host'u çözer; tek bir IP bile herkese açık değilse reddeder."""
        ips = await self.resolve(host, port)
        if not all(is_public_ip(ip) for ip in ips):
            raise BlockedAddressError()
        return ips

    async def check_url(self, url: str) -> str:
        """Şema/port/host'u doğrular ve host'u çözer. URL'yi fragment'sız hâliyle döner
        (query korunur: JS chunk'ları ?v=... ile istenebilir)."""
        target = normalize_target_url(url)
        await self.resolve_public(target.host, target.port)
        return urldefrag(url.strip()).url


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, guard: UrlGuard, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._guard = guard
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 (httpcore arayüzü)
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if port not in ALLOWED_PORTS:
            raise httpcore.ConnectError("port not allowed")
        try:
            ips = await self._guard.resolve_public(host, port)
        except TargetValidationError as exc:
            raise httpcore.ConnectError(exc.code) from exc

        last_error: Exception | None = None
        for ip in ips:
            try:
                stream = await self._inner.connect_tcp(
                    str(ip),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
                continue
            self._verify_peer(stream)
            return stream
        raise httpcore.ConnectError("connect failed") from last_error

    @staticmethod
    def _verify_peer(stream: httpcore.AsyncNetworkStream) -> None:
        peer = stream.get_extra_info("server_addr")
        ip = parse_ip(str(peer[0])) if peer else None
        if ip is None or not is_public_ip(ip):
            raise httpcore.ConnectError("url_not_public")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    """httpx transport'u: tüm TCP bağlantıları `GuardedNetworkBackend` üzerinden açılır.

    Not: httpx 0.28 `network_backend` parametresi sunmadığı için iç havuz değiştirilir.
    httpx sürümü yükseltilirse tests/security/test_ssrf.py bu varsayımı doğrular.
    """

    def __init__(
        self, guard: UrlGuard, inner_backend: httpcore.AsyncNetworkBackend | None = None
    ) -> None:
        super().__init__(trust_env=False, retries=0)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=10,
            max_keepalive_connections=5,
            retries=0,
            network_backend=GuardedNetworkBackend(guard, inner_backend),
        )
