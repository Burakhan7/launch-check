"""Web collector: ana sayfa + aynı origin JS dosyaları + lazy chunk'lar → WebContext."""

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from launchcheck.checks.base import ScanContext, TargetType
from launchcheck.collectors.base import CollectorError
from launchcheck.collectors.fetcher import FetchError, FetchResult, SafeFetcher
from launchcheck.collectors.platform import backend_hints, detect_platforms
from launchcheck.collectors.web_assets import (
    extract_chunk_refs,
    extract_html_scripts,
    origin_of,
    pick_crawl_links,
    split_internal_links,
)
from launchcheck.collectors.web_signals import analyze_content
from launchcheck.config import Settings
from launchcheck.security.ssrf import UrlGuard
from launchcheck.security.url import normalize_target_url


@dataclass(frozen=True)
class ScriptFile:
    url: str  # inline script'ler için "<sayfa url>#inline-N"
    content: str
    truncated: bool = False
    inline: bool = False


@dataclass(frozen=True)
class PageSnapshot:
    """Taranan bir HTML sayfası. `pages[0]` her zaman ana sayfadır."""

    url: str
    status: int
    headers: dict[str, str]
    html: str


class Fetcher(Protocol):
    async def fetch(
        self, url: str, max_bytes: int, follow_redirects: bool = True, cookieless: bool = False
    ) -> FetchResult: ...

    async def aclose(self) -> None: ...


@dataclass
class WebContext(ScanContext):
    requested_url: str = ""
    final_url: str = ""
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    html: str = ""
    scripts: list[ScriptFile] = field(default_factory=list)
    all_script_urls: list[str] = field(default_factory=list)
    # Ana sayfa + aynı origin iç sayfalar (sadece 200 + text/html olanlar)
    pages: list[PageSnapshot] = field(default_factory=list)
    # Ana sayfanın çerezsiz yanıtları (ilki ana sayfa isteğinin kendisi). Her ziyaretçiye
    # otomatik hesap/oturum açılıyor mu karşılaştırması için.
    cookieless_samples: list[PageSnapshot] = field(default_factory=list)
    fetcher: Fetcher | None = None
    probe_max_bytes: int = 0
    # Eşzamanlı check'ler aynı yolu isterse tek istek atılır.
    _fetch_tasks: dict[tuple[str, bool], "asyncio.Task[FetchResult | None]"] = field(
        default_factory=dict
    )
    _baseline_path: str = field(default_factory=lambda: f"/{uuid.uuid4().hex}.txt")

    @property
    def origin(self) -> str:
        return origin_of(self.final_url)

    @property
    def is_https(self) -> bool:
        return self.final_url.startswith("https://")

    async def _fetch_or_none(self, url: str, follow_redirects: bool) -> FetchResult | None:
        if self.fetcher is None:
            return None
        try:
            return await self.fetcher.fetch(url, self.probe_max_bytes, follow_redirects)
        except FetchError:
            return None

    async def fetch_url(self, url: str, follow_redirects: bool = False) -> FetchResult | None:
        """Tek bir URL'yi çeker (önbellekli, SSRF korumalı). Ağ hatasında None döner;
        check'ler bunu "bulunamadı" sayar."""
        key = (url, follow_redirects)
        if key not in self._fetch_tasks:
            self._fetch_tasks[key] = asyncio.ensure_future(
                self._fetch_or_none(url, follow_redirects)
            )
        return await asyncio.shield(self._fetch_tasks[key])

    async def fetch_uncached(self, url: str, max_bytes: int) -> FetchResult | None:
        """Önbelleğe ALINMAYAN tek istek (redirect takip eder, SSRF korumalı). Yanıt gövdesi
        bellekte tutulmamalı olan içerikler için (ör. kullanıcı görselleri)."""
        if self.fetcher is None:
            return None
        try:
            return await self.fetcher.fetch(url, max_bytes, True)
        except FetchError:
            return None

    async def probe(self, path: str) -> FetchResult | None:
        """Aynı origin'de bir yolu redirect takip etmeden çeker."""
        return await self.fetch_url(self.origin + path)

    async def baseline(self) -> FetchResult | None:
        """Var olmayan rastgele bir yolun yanıtı (SPA catch-all tespiti için)."""
        return await self.probe(self._baseline_path)

    async def real_file(self, path: str) -> FetchResult | None:
        """Yol gerçekten bir dosya döndürüyorsa yanıtı, aksi hâlde None.
        200 olmayanlar ve SPA'nın her yola döndürdüğü sayfayla aynı içerik elenir."""
        res = await self.probe(path)
        if res is None or res.status != 200 or not res.body:
            return None
        base = await self.baseline()
        if base is not None and base.status == 200 and base.body == res.body:
            return None
        return res

    async def aclose(self) -> None:
        for task in self._fetch_tasks.values():
            task.cancel()
        if self.fetcher is not None:
            await self.fetcher.aclose()
            self.fetcher = None


CONTENT_SIGNALS_KEY = "content_signals"
HTTP_ERROR_MIN = 400
HTTP_OK = 200
BOT_CHALLENGE_MARKERS = (
    b"<title>just a moment",  # Cloudflare
    b"cf-chl-",
    b"challenge-platform",
    b"captcha-delivery.com",  # DataDome
    b"_incapsula_resource",  # Imperva
    b"<title>attention required",
    b"<title>vercel security checkpoint",
)
# Header adı → "challenge" değeri: Cloudflare, Vercel
CHALLENGE_HEADERS = ("cf-mitigated", "x-vercel-mitigated")


def is_bot_challenge(res: FetchResult) -> bool:
    if any(res.headers.get(h, "").lower() == "challenge" for h in CHALLENGE_HEADERS):
        return True
    head = res.body[:20000].lower()
    return any(marker in head for marker in BOT_CHALLENGE_MARKERS)


def raise_for_unscannable(main: FetchResult) -> None:
    """Ana sayfa hata veya bot doğrulama sayfası dönüyorsa yanıltıcı rapor üretme."""
    if is_bot_challenge(main):
        raise CollectorError("site_blocked_bot")
    if main.status >= HTTP_ERROR_MIN:
        raise CollectorError("site_http_error")


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
        raise_for_unscannable(main)
        html = main.text
        page = extract_html_scripts(html, main.final_url)
        scripts = [
            ScriptFile(url=f"{main.final_url}#inline-{i}", content=s, inline=True)
            for i, s in enumerate(page.inline)
        ]
        scripts += await self._fetch_scripts(fetcher, page.external, origin_of(main.final_url))

        home = PageSnapshot(main.final_url, main.status, main.headers, html)
        inner, extra_samples = await asyncio.gather(
            self._fetch_pages(fetcher, home), self._cookieless_samples(fetcher, home.url)
        )
        pages = [home, *inner]
        signals = analyze_content(
            ((p.url, p.html) for p in pages), [*page.all_external, *(s.content for s in scripts)]
        )

        external = [s for s in scripts if not s.inline]
        sources = [html] + [s.content for s in scripts]
        meta: dict[str, Any] = {
            "platform": detect_platforms(
                main.final_url, main.headers, html, page.all_external, (s.content for s in scripts)
            ),
            "js_files_scanned": len(external),
            "js_truncated": any(s.truncated for s in external) or main.truncated,
            "pages_scanned": len(pages),
            **signals.flags(),
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
            pages=pages,
            cookieless_samples=[home, *extra_samples],
            fetcher=fetcher,
            probe_max_bytes=self._settings.probe_max_bytes,
            cache={CONTENT_SIGNALS_KEY: signals},
        )

    async def _cookieless_samples(self, fetcher: SafeFetcher, url: str) -> list[PageSnapshot]:
        """Ana sayfayı çerez göndermeden birkaç kez daha çeker (hata olanlar atlanır)."""
        count = self._settings.web_cookieless_extra_samples
        results = await asyncio.gather(
            *(
                fetcher.fetch(url, self._settings.max_html_bytes, cookieless=True)
                for _ in range(count)
            ),
            return_exceptions=True,
        )
        return [
            PageSnapshot(r.final_url, r.status, r.headers, r.text)
            for r in results
            if isinstance(r, FetchResult) and r.status == HTTP_OK
        ]

    async def _fetch_page(
        self, fetcher: SafeFetcher, url: str, origin: str, sem: asyncio.Semaphore
    ) -> PageSnapshot | None:
        async with sem:
            try:
                res = await fetcher.fetch(url, self._settings.max_html_bytes)
            except FetchError:
                return None
        # 200 olmayan, HTML olmayan veya başka origin'e yönlenen sayfalar atlanır.
        if res.status != HTTP_OK or res.content_type != "text/html":
            return None
        if origin_of(res.final_url) != origin:
            return None
        return PageSnapshot(res.final_url, res.status, res.headers, res.text)

    async def _fetch_pages(self, fetcher: SafeFetcher, home: PageSnapshot) -> list[PageSnapshot]:
        """Ana sayfadaki iç linklerden en fazla `web_max_internal_pages` sayfa çeker.
        Süre sınırı dolarsa bitmeyen istekler iptal edilir ve elde olanlarla devam edilir."""
        limit = self._settings.web_max_internal_pages
        priority, content = split_internal_links(home.html, home.url, exclude=(home.url,))
        links = pick_crawl_links(
            priority, content, limit, self._settings.web_internal_content_link_slots
        )
        if not links:
            return []
        origin = origin_of(home.url)
        sem = asyncio.Semaphore(self._settings.js_fetch_concurrency)
        tasks = [asyncio.ensure_future(self._fetch_page(fetcher, u, origin, sem)) for u in links]
        _, pending = await asyncio.wait(tasks, timeout=self._settings.web_internal_pages_timeout_s)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        seen = {home.url.rstrip("/")}
        pages = []
        for task in tasks:  # link sırası (öncelik) korunur
            if task.cancelled() or task.exception() is not None:
                continue
            snapshot = task.result()
            if snapshot is not None and snapshot.url.rstrip("/") not in seen:
                seen.add(snapshot.url.rstrip("/"))
                pages.append(snapshot)
        return pages

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
