"""Testler için 127.0.0.1 üzerinde küçük HTTP sunucusu ve "kanarya" dinleyici.

Gerçek ağa çıkılmaz: tarayıcı/proxy testleri bu yerel sunuculara, sadece testlerde geçerli
SSRF allowlist'iyle (IP, port) bağlanır.
"""

import asyncio
import contextlib
from dataclasses import dataclass, field


@dataclass
class Route:
    body: bytes
    content_type: str = "text/html; charset=utf-8"
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    delay_s: float = 0  # yavaş sayfa benzetimi (huni testleri)


class LocalHttpServer:
    def __init__(
        self, routes: dict[str, Route] | None = None, fallback: Route | None = None
    ) -> None:
        self.routes = routes or {}
        self.fallback = fallback or Route(b"not found", "text/plain", 404)
        self.requests: list[bytes] = []  # ham istek başlıkları
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.sockets[0].getsockname()[1])

    async def __aenter__(self) -> "LocalHttpServer":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            self.requests.append(head)
            path = head.split(b" ", 2)[1].decode()
            route = self.routes.get(path.split("?", 1)[0], self.fallback)
            if route.delay_s:
                await asyncio.sleep(route.delay_s)
            extra = "".join(f"{k}: {v}\r\n" for k, v in route.headers.items())
            writer.write(
                f"HTTP/1.1 {route.status} X\r\nContent-Type: {route.content_type}\r\n"
                f"Content-Length: {len(route.body)}\r\n{extra}Connection: close\r\n\r\n".encode()
                + route.body
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


class Canary:
    """Hiçbir bağlantı almaması gereken dinleyici (proxy'yi atlayan istek var mı?)."""

    def __init__(self) -> None:
        self.hits = 0
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.sockets[0].getsockname()[1])

    async def __aenter__(self) -> "Canary":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.hits += 1
        writer.close()
