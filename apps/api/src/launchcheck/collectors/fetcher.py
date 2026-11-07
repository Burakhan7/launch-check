"""SSRF korumalı, limitli HTTP istemcisi. Web collector ve web check'leri bunu kullanır."""

from dataclasses import dataclass, field
from email.message import Message
from urllib.parse import urljoin

import httpx

from launchcheck.config import Settings
from launchcheck.security.ssrf import GuardedTransport, UrlGuard
from launchcheck.security.url import TargetValidationError

REDIRECT_STATUSES = (301, 302, 303, 307, 308)
# Tarayıcı benzeri Accept: bazı siteler "*/*" isteyen istemcilere HTML yerine markdown döner
# (ör. AI ajanları için). Kullanıcının gördüğü sayfayı taramak istiyoruz.
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


class FetchError(Exception):
    """`code` i18n `errors` anahtarıdır."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    headers: dict[str, str]  # anahtarlar küçük harf; tekrar eden header'lar ", " ile birleşik
    body: bytes
    truncated: bool = False
    redirects: list[str] = field(default_factory=list)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    @property
    def text(self) -> str:
        msg = Message()
        msg["content-type"] = self.headers.get("content-type", "")
        charset = msg.get_content_charset() or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def _flatten_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k.lower(): ", ".join(headers.get_list(k)) for k in headers}


class SafeFetcher:
    def __init__(
        self,
        settings: Settings,
        guard: UrlGuard,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._guard = guard
        self._budget = settings.max_total_bytes
        self._client = httpx.AsyncClient(
            transport=transport or GuardedTransport(guard),
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(
                settings.http_request_timeout_s, connect=settings.http_connect_timeout_s
            ),
            headers={"User-Agent": settings.bot_user_agent, "Accept": BROWSER_ACCEPT},
        )

    @property
    def budget_remaining(self) -> int:
        return self._budget

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _validate(self, url: str) -> str:
        try:
            return await self._guard.check_url(url)
        except TargetValidationError as exc:
            raise FetchError(exc.code) from exc

    async def fetch(
        self, url: str, max_bytes: int, follow_redirects: bool = True, cookieless: bool = False
    ) -> FetchResult:
        """`cookieless=True`: istemcinin çerez kavanozundaki çerezler gönderilmez (ilk ziyaret
        eden biri gibi)."""
        current = await self._validate(url)
        redirects: list[str] = []
        for _ in range(self._settings.max_redirects + 1):
            result = await self._get_once(current, max_bytes, cookieless)
            location = result.headers.get("location")
            if not (follow_redirects and result.status in REDIRECT_STATUSES and location):
                result.url, result.redirects = url, redirects
                return result
            current = await self._validate(urljoin(current, location))
            redirects.append(current)
        raise FetchError("too_many_redirects")

    async def _get_once(self, url: str, max_bytes: int, cookieless: bool = False) -> FetchResult:
        limit = min(max_bytes, self._budget)
        try:
            request = self._client.build_request("GET", url)
            if cookieless:
                request.headers.pop("cookie", None)
            resp = await self._client.send(request, stream=True)
            try:
                body, truncated = await self._read_limited(resp, limit)
            finally:
                await resp.aclose()
            return FetchResult(
                url=url,
                final_url=str(resp.url),
                status=resp.status_code,
                headers=_flatten_headers(resp.headers),
                body=body,
                truncated=truncated,
            )
        except httpx.ConnectError as exc:
            code = "url_not_public" if "url_not_public" in str(exc) else "site_unreachable"
            raise FetchError(code) from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise FetchError("site_unreachable") from exc

    async def _read_limited(self, resp: httpx.Response, limit: int) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        size = 0
        truncated = False
        async for chunk in resp.aiter_bytes():
            if size + len(chunk) > limit:
                chunks.append(chunk[: limit - size])
                size = limit
                truncated = True
                break
            chunks.append(chunk)
            size += len(chunk)
        self._budget -= size
        return b"".join(chunks), truncated
