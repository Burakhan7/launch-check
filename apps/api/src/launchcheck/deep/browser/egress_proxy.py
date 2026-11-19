"""SSRF korumalı HTTP/CONNECT proxy (docs/PHASE2.md §12.1).

Tarayıcı (Chromium, Lighthouse'un Chromium'u) TÜM trafiğini buradan geçirir. Sayfanın JS'i
`http://169.254.169.254`, `ws://redis:6379` veya iç IP'lere istek atmaya çalışsa bile:

- Her istekte (HTTP ve CONNECT; WebSocket de CONNECT ile gelir) host BİR KEZ çözülür,
  **tüm** IP'ler `security/ssrf.py` kurallarıyla kontrol edilir ve **doğrulanan IP'ye**
  bağlanılır (DNS rebinding'e karşı: ikinci bir çözümleme yapılmaz).
- Sadece 80/443 portları (`allow` ile testlerde yerel test sunucusu hariç).
- Tarama başına toplam yanıt baytı sınırı; aşılınca yeni istekler reddedilir.

Reddedilen istekler `blocked` listesinde tutulur (sadece host, port ve neden; yol/sorgu yok).
Hiçbir istek/yanıt içeriği loglanmaz.
"""

import asyncio
import contextlib
import ipaddress
import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from launchcheck.security.ssrf import IPAddress, UrlGuard, is_public_ip
from launchcheck.security.url import TargetValidationError

logger = logging.getLogger(__name__)

ALLOWED_PORTS = frozenset({80, 443})
MAX_HEAD_BYTES = 64 * 1024
HEAD_TIMEOUT_S = 15.0
CONNECT_TIMEOUT_S = 10.0
IDLE_TIMEOUT_S = 30.0
CHUNK = 64 * 1024
# Proxy'nin kendi reddettiği yanıtlar bu başlığı taşır: render bunları site hatası saymaz
BLOCKED_HEADER = "X-LaunchCheck-Blocked"
HOP_BY_HOP = frozenset(
    {"proxy-connection", "proxy-authorization", "proxy-authenticate", "keep-alive", "te"}
)


class Reason:
    PRIVATE_ADDRESS = "private_address"
    PORT = "port_not_allowed"
    UNRESOLVABLE = "unresolvable"
    SCHEME = "scheme_not_allowed"
    BUDGET = "budget_exceeded"
    BAD_REQUEST = "bad_request"
    CONNECT_FAILED = "connect_failed"


@dataclass(frozen=True)
class Blocked:
    host: str
    port: int
    reason: str


@dataclass
class ProxyStats:
    allowed: int = 0
    bytes_in: int = 0  # hedeflerden tarayıcıya giden toplam bayt
    blocked: list[Blocked] = field(default_factory=list)


class _Rejected(Exception):
    def __init__(self, status: int, reason: str, host: str = "", port: int = 0) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.host = host
        self.port = port


@dataclass(frozen=True)
class _Request:
    method: str
    host: str
    port: int
    head: bytes  # hedefe gönderilecek (yeniden yazılmış) başlık; CONNECT'te boş
    connect: bool


def _split_host_port(authority: str, default_port: int) -> tuple[str, int]:
    authority = authority.strip()
    if authority.startswith("["):  # [::1]:443
        host, _, rest = authority[1:].partition("]")
        port_text = rest[1:] if rest.startswith(":") else ""
    else:
        host, sep, port_text = authority.rpartition(":")
        if not sep:
            host, port_text = authority, ""
    if not host:
        raise _Rejected(400, Reason.BAD_REQUEST)
    try:
        port = int(port_text) if port_text else default_port
    except ValueError as exc:
        raise _Rejected(400, Reason.BAD_REQUEST, host) from exc
    if not 0 < port < 65536:
        raise _Rejected(400, Reason.BAD_REQUEST, host)
    return host.lower(), port


def _rewrite_head(lines: list[str], path: str) -> bytes:
    """Mutlak URI'yi origin biçimine çevirir, proxy başlıklarını atar. Upgrade (WebSocket)
    yoksa `Connection: close`: aynı proxy bağlantısında başka host'a istek taşınmasın."""
    method, _, version = lines[0].split(" ", 2)
    headers = []
    upgrade = False
    for line in lines[1:]:
        name, _, value = line.partition(":")
        key = name.strip().lower()
        if key in HOP_BY_HOP:
            continue
        if key == "upgrade":
            upgrade = True
        if key == "connection":
            continue
        headers.append(f"{name.strip()}: {value.strip()}")
    headers.append("Connection: Upgrade" if upgrade else "Connection: close")
    return ("\r\n".join([f"{method} {path} {version}", *headers]) + "\r\n\r\n").encode("latin-1")


def parse_request(head: bytes) -> _Request:
    try:
        text = head.decode("latin-1")
    except UnicodeDecodeError as exc:  # pragma: no cover - latin-1 her baytı çözer
        raise _Rejected(400, Reason.BAD_REQUEST) from exc
    lines = [line for line in text.split("\r\n") if line]
    if not lines or lines[0].count(" ") < 2:
        raise _Rejected(400, Reason.BAD_REQUEST)
    method, target, _ = lines[0].split(" ", 2)
    if method.upper() == "CONNECT":
        host, port = _split_host_port(target, 443)
        return _Request("CONNECT", host, port, b"", True)
    parts = urlsplit(target)
    if parts.scheme.lower() not in ("http", "ws"):
        # https/wss CONNECT ile gelir; mutlak olmayan URI veya başka şema kabul edilmez
        raise _Rejected(400, Reason.SCHEME, parts.hostname or "")
    host, port = _split_host_port(parts.netloc.rsplit("@", 1)[-1], 80)
    path = parts.path or "/"
    if parts.query:
        path += f"?{parts.query}"
    return _Request(method, host, port, _rewrite_head(lines, path), False)


class EgressProxy:
    def __init__(
        self,
        guard: UrlGuard,
        max_total_bytes: int,
        allow: frozenset[tuple[str, int]] = frozenset(),
        allowed_ports: frozenset[int] = ALLOWED_PORTS,
    ) -> None:
        """`allow`: SADECE testlerde kullanılır; (IP, port) çiftleri SSRF kuralının dışında
        tutulur (127.0.0.1 üzerindeki yerel test sunucusu)."""
        self._guard = guard
        self._max_total_bytes = max_total_bytes
        self._allow = allow
        self._allowed_ports = allowed_ports
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.stats = ProxyStats()

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("proxy başlatılmadı")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> int:
        # Sadece loopback'te dinler; container dışından erişilemez.
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.port

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def __aenter__(self) -> "EgressProxy":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # --- bağlantı işleme

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._serve(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, TimeoutError, OSError):
            pass
        finally:
            if task is not None:
                self._tasks.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(HEAD_TIMEOUT_S):
                head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.LimitOverrunError:
            await self._reject(writer, _Rejected(400, Reason.BAD_REQUEST))
            return
        if len(head) > MAX_HEAD_BYTES:
            await self._reject(writer, _Rejected(400, Reason.BAD_REQUEST))
            return
        try:
            request = parse_request(head)
            ip = await self._authorize(request.host, request.port)
            upstream_reader, upstream_writer = await self._open(request, ip)
        except _Rejected as rejected:
            await self._reject(writer, rejected)
            return
        self.stats.allowed += 1
        try:
            if request.connect:
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                upstream_writer.write(request.head)
                await upstream_writer.drain()
            await asyncio.gather(
                self._pipe(reader, upstream_writer, count=False),
                self._pipe(upstream_reader, writer, count=True),
            )
        finally:
            upstream_writer.close()
            with contextlib.suppress(Exception):
                await upstream_writer.wait_closed()

    async def _authorize(self, host: str, port: int) -> IPAddress:
        if self.stats.bytes_in >= self._max_total_bytes:
            raise _Rejected(403, Reason.BUDGET, host, port)
        try:
            ips = await self._guard.resolve(host, port)
        except TargetValidationError as exc:
            raise _Rejected(403, Reason.UNRESOLVABLE, host, port) from exc
        allowed = [(str(ip), port) in self._allow for ip in ips]
        if all(allowed):  # sadece testler: yerel test sunucusu
            return ips[0]
        if port not in self._allowed_ports:
            raise _Rejected(403, Reason.PORT, host, port)
        if not all(is_public_ip(ip) or ok for ip, ok in zip(ips, allowed, strict=True)):
            raise _Rejected(403, Reason.PRIVATE_ADDRESS, host, port)
        return ips[0]

    async def _open(
        self, request: _Request, ip: IPAddress
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Host adına değil, doğrulanmış IP'ye bağlanır (DNS rebinding'e karşı)."""
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_S):
                reader, writer = await asyncio.open_connection(str(ip), request.port)
        except (OSError, TimeoutError) as exc:
            raise _Rejected(502, Reason.CONNECT_FAILED, request.host, request.port) from exc
        peer = writer.get_extra_info("peername")
        peer_ip = ipaddress.ip_address(peer[0]) if peer else None
        if peer_ip != ip:
            writer.close()
            raise _Rejected(403, Reason.PRIVATE_ADDRESS, request.host, request.port)
        return reader, writer

    async def _pipe(
        self, source: asyncio.StreamReader, sink: asyncio.StreamWriter, count: bool
    ) -> None:
        try:
            while True:
                async with asyncio.timeout(IDLE_TIMEOUT_S):
                    data = await source.read(CHUNK)
                if not data:
                    break
                if count:
                    self.stats.bytes_in += len(data)
                    if self.stats.bytes_in > self._max_total_bytes:
                        break  # bütçe doldu: bağlantı kesilir
                sink.write(data)
                await sink.drain()
        except (ConnectionError, TimeoutError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                if sink.can_write_eof():
                    sink.write_eof()

    async def _reject(self, writer: asyncio.StreamWriter, rejected: _Rejected) -> None:
        self.stats.blocked.append(Blocked(rejected.host, rejected.port, rejected.reason))
        status = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}[rejected.status]
        writer.write(
            f"HTTP/1.1 {rejected.status} {status}\r\nContent-Length: 0\r\n"
            f"{BLOCKED_HEADER}: {rejected.reason}\r\nConnection: close\r\n\r\n".encode()
        )
        with contextlib.suppress(Exception):
            await writer.drain()
