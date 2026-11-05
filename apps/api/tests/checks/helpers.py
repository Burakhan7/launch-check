"""Check testleri için yardımcılar: ağsız WebContext ve test anında üretilen sahte secret'lar.

Sahte anahtarlar dosyaya düz yazılmaz (GitHub secret scanning ve kendi tarayıcımız tetiklenmesin).
"""

import base64
import json
import random
import string
from typing import Any

from launchcheck.checks.base import Check, Finding, TargetType
from launchcheck.collectors.fetcher import FetchResult
from launchcheck.collectors.web_collector import ScriptFile, WebContext

SITE = "https://app.example.com"
ALNUM = string.ascii_letters + string.digits
HEX = "0123456789abcdef"
SPA_HTML = (
    b"<!doctype html><html><head><title>App</title></head><body><div id=root></div></body></html>"
)


def fake_token(prefix: str, length: int, alphabet: str = ALNUM, seed: int = 1) -> str:
    rng = random.Random(f"{prefix}-{length}-{seed}")
    return prefix + "".join(rng.choice(alphabet) for _ in range(length))


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def fake_supabase_jwt(role: str, ref: str = "abcdefghijklmnopqrst") -> str:
    header = _b64({"alg": "HS256", "typ": "JWT"})
    payload = _b64({"iss": "supabase", "ref": ref, "role": role, "iat": 1700000000})
    return f"{header}.{payload}.{fake_token('', 43, ALNUM + '-_')}"


def resp(
    url: str,
    status: int = 200,
    body: bytes | str = b"",
    content_type: str = "text/plain",
    headers: dict[str, str] | None = None,
) -> FetchResult:
    raw = body.encode() if isinstance(body, str) else body
    return FetchResult(
        url=url,
        final_url=url,
        status=status,
        headers={"content-type": content_type, **(headers or {})},
        body=raw,
    )


class FakeFetcher:
    """URL → yanıt. Tanımsız URL'ler: `spa=True` ise 200 + index.html (catch-all), değilse 404."""

    def __init__(self, routes: dict[str, FetchResult] | None = None, spa: bool = False) -> None:
        self.routes = routes or {}
        self.spa = spa
        self.calls: list[str] = []

    async def fetch(self, url: str, max_bytes: int, follow_redirects: bool = True) -> FetchResult:
        self.calls.append(url)
        if url in self.routes:
            return self.routes[url]
        if self.spa:
            return resp(url, 200, SPA_HTML, "text/html")
        return resp(url, 404, "Not Found", "text/html")

    async def aclose(self) -> None:
        return None


def make_ctx(
    html: str = "<html><head><title>T</title></head><body></body></html>",
    headers: dict[str, str] | None = None,
    scripts: dict[str, str] | None = None,
    routes: dict[str, FetchResult] | None = None,
    final_url: str = f"{SITE}/",
    spa: bool = False,
    inline: list[str] | None = None,
) -> WebContext:
    files = [ScriptFile(url=u, content=c) for u, c in (scripts or {}).items()]
    files += [
        ScriptFile(url=f"{final_url}#inline-{i}", content=c, inline=True)
        for i, c in enumerate(inline or [])
    ]
    return WebContext(
        target_type=TargetType.WEB,
        target_label=SITE,
        requested_url=final_url,
        final_url=final_url,
        status=200,
        headers={k.lower(): v for k, v in (headers or {}).items()},
        html=html,
        scripts=files,
        fetcher=FakeFetcher(routes, spa=spa),
        probe_max_bytes=1024 * 1024,
    )


async def run(check: type[Check], ctx: WebContext) -> list[Finding]:
    return await check().run(ctx)
