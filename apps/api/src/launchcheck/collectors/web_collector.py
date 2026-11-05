"""Web collector: ana sayfa + aynı origin JS dosyaları + lazy chunk'lar → WebContext."""

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import httpx

from launchcheck.checks.base import ScanContext, TargetType
from launchcheck.collectors.base import CollectorError
from launchcheck.collectors.fetcher import FetchError, FetchResult, SafeFetcher
from launchcheck.collectors.platform import backend_hints, detect_platforms
from launchcheck.collectors.web_assets import (
    extract_chunk_refs,
    extract_html_scripts,
    origin_of,
)
from launchcheck.config import Settings
from launchcheck.security.ssrf import UrlGuard
from launchcheck.security.url import normalize_target_url


@dataclass(frozen=True)
class ScriptFile:
    url: str  # inline script'ler için "<sayfa url>#inline-N"
    content: str
    truncated: bool = False
    inline: bool = False


@dataclass
class WebContext(ScanContext):
    requested_url: str = ""
    final_url: str = ""
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    html: str = ""
    scripts: list[ScriptFile] = field(default_factory=list)
    all_script_urls: list[str] = field(default_factory=list)
    fetcher: SafeFetcher | None = None
    probe_max_bytes: int = 0
    _probe_cache: dict[str, FetchResult | None] = field(default_factory=dict)

    @property
    def origin(self) -> str:
        return origin_of(self.final_url)

    async def probe(self, path: str) -> FetchResult | None:
        """Aynı origin'de tek bir yolu redirect takip etmeden çeker (önbellekli).
        Ağ hatasında None döner; check'ler bunu "bulunamadı" sayar."""
        if path in self._probe_cache:
            return self._probe_cache[path]
        result: FetchResult | None = None
        if self.fetcher is not None:
            try:
                result = await self.fetcher.fetch(
                    self.origin + path, self.probe_max_bytes, follow_redirects=False
                )
            except FetchError:
                result = None
        self._probe_cache[path] = result
        return result

    async def aclose(self) -> None:
        if self.fetcher is not None:
            await self.fetcher.aclose()
            self.fetcher = None


class WebCollector:
    def __init__(
        self,
        settings: Settings,
        guard: UrlGuard,
        transport_factory: Any = None,  # testler için: () -> httpx.AsyncBaseTransport
    ) -> None:
        self._settings = settings
        self._guard = guard
        self._transport_factory = transport_factory

    def _new_fetcher(self) -> SafeFetcher:
        transport: httpx.AsyncBaseTransport | None = (
            self._transport_factory() if self._transport_factory else None
        )
        return SafeFetcher(self._settings, self._guard, transport)

    async def collect(self, scan_id: uuid.UUID, args: dict[str, Any]) -> ScanContext:
        fetcher = self._new_fetcher()
        try:
            return await self._collect(fetcher, str(args["url"]))
        except FetchError as exc:
            await fetcher.aclose()
            raise CollectorError(exc.code) from exc
        except BaseException:
            await fetcher.aclose()
            raise

    async def _collect(self, fetcher: SafeFetcher, url: str) -> WebContext:
        main = await fetcher.fetch(url, self._settings.max_html_bytes)
        html = main.text
        page = extract_html_scripts(html, main.final_url)
        scripts = [
            ScriptFile(url=f"{main.final_url}#inline-{i}", content=s, inline=True)
            for i, s in enumerate(page.inline)
        ]
        scripts += await self._fetch_scripts(fetcher, page.external, origin_of(main.final_url))

        external = [s for s in scripts if not s.inline]
        sources = [html] + [s.content for s in scripts]
        meta: dict[str, Any] = {
            "platform": detect_platforms(
                main.final_url, main.headers, html, page.all_external, (s.content for s in scripts)
            ),
            "js_files_scanned": len(external),
            "js_truncated": any(s.truncated for s in external) or main.truncated,
            **backend_hints(sources),
        }
        return WebContext(
            target_type=TargetType.WEB,
            target_label=normalize_target_url(url).origin,
            meta=meta,
            requested_url=url,
            final_url=main.final_url,
            status=main.status,
            headers=main.headers,
            html=html,
            scripts=scripts,
            all_script_urls=page.all_external,
            fetcher=fetcher,
            probe_max_bytes=self._settings.probe_max_bytes,
        )

    async def _fetch_one(
        self, fetcher: SafeFetcher, url: str, sem: asyncio.Semaphore
    ) -> ScriptFile | None:
        async with sem:
            if fetcher.budget_remaining <= 0:
                return None
            try:
                res = await fetcher.fetch(url, self._settings.max_js_file_bytes)
            except FetchError:
                return None
        if res.status != 200 or "html" in res.content_type:  # SPA catch-all index.html'i ele
            return None
        return ScriptFile(url=res.final_url, content=res.text, truncated=res.truncated)

    async def _fetch_scripts(
        self, fetcher: SafeFetcher, initial: list[str], origin: str
    ) -> list[ScriptFile]:
        """BFS: HTML'deki script'ler, sonra onların içindeki chunk'lar. Limitler dahilinde."""
        sem = asyncio.Semaphore(self._settings.js_fetch_concurrency)
        limit = self._settings.max_js_files
        queue = deque(initial)
        seen: set[str] = set(initial)
        out: list[ScriptFile] = []
        attempted = 0
        while queue and attempted < limit and fetcher.budget_remaining > 0:
            batch = [queue.popleft() for _ in range(min(len(queue), limit - attempted))]
            attempted += len(batch)
            results = await asyncio.gather(*(self._fetch_one(fetcher, u, sem) for u in batch))
            for script in results:
                if script is None:
                    continue
                out.append(script)
                for ref in extract_chunk_refs(script.content, script.url, origin):
                    if ref not in seen:
                        seen.add(ref)
                        queue.append(ref)
        return out
