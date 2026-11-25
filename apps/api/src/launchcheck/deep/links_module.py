"""Link, meta ve yapılandırılmış veri modülü (ağ şeridi, PHASE2 §6.4).

Faz 1'in pasif olduğu için ertelediği doğrulamalar. Altı bağımsız parça paralel çalışır; biri
başarısız olursa sadece o parçanın check'leri atlanır (raporda "geçti" sayılmaz):
- internal: taranan sayfalardaki iç linkler (render'ın açtığı sayfaların durumu tekrar istenmez)
- external: dış linklerden en fazla `deep_links_max_external` tanesi (aynı host'a saniyede 1)
- https_targets: Faz 1'in `insecure_http_links` bulgusundaki http:// hedefleri HTTPS destekliyor mu
- youtube: sayfadaki videolar YouTube oEmbed ile hâlâ var mı / gömülebiliyor mu
- og_image: paylaşım görseli gerçekten yükleniyor mu (boyut, oran, dosya büyüklüğü)
- structured / sd_opportunity: JSON-LD hataları ve eksik yapılandırılmış veri fırsatları

Linkler render modülünün gezdiği sayfalardan gelir; render verisi yoksa Faz 1 sayfalarından.
Pasiflik: istekler normal bir ziyaretçinin linke tıklamasına eşdeğerdir (GET/HEAD, sorgu dizesi
yok); oturum kapatma, sepete ekleme gibi durum değiştiren yollar istenmez.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser

from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.checks.shared.domains import registered_domain
from launchcheck.checks.shared.text import tokens
from launchcheck.collectors.fetcher import FetchError, FetchResult, SafeFetcher
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import Settings, get_settings
from launchcheck.deep.context import DeepContext
from launchcheck.deep.external.services import (
    NoLimit,
    RedisServiceLimiter,
    ServiceLimiter,
)
from launchcheck.deep.links.media import (
    image_size,
    oembed_url,
    og_issues,
    video_id,
    video_state,
)
from launchcheck.deep.links.probe import HostPacer, probe_link
from launchcheck.deep.links.structured import analyze_blocks, microdata_types, opportunities
from launchcheck.deep.links_data import (
    HttpsTarget,
    JsonLdPage,
    LinkOutcome,
    LinksData,
    LinkStatus,
    OgImage,
    PartStatus,
    VideoStatus,
)
from launchcheck.deep.modules import DeepModule, Lane, ModuleOutput
from launchcheck.deep.render_data import RenderedPage, SdSignals

logger = logging.getLogger(__name__)

CHECK_BUDGET_S = 8  # check'ler ve kapanış için ayrılan süre
INSECURE_LINKS_CHECK = "web.links.insecure_http_links"
MAX_HTTPS_TARGETS = 20
MAX_PAGES_PER_LINK = 5
SKIP_EXTENSIONS = (".pdf", ".zip", ".dmg", ".exe", ".apk", ".mp4", ".mov", ".mp3")
OG_TAGS = ("og:image", "twitter:image")
FETCH_TOTAL_BYTES = 64 * 1024 * 1024


class LinksUnavailableError(Exception):
    """Ne render verisi ne Faz 1 sayfaları var: kontrol edilecek link yok."""


@dataclass(frozen=True)
class _Page:
    """Linklerin toplandığı sayfa (render veya Faz 1 sunucu HTML'i)."""

    url: str
    internal: list[str]
    priority: set[str]
    external: dict[str, str]  # url → chrome | content
    og: dict[str, str]
    og_source: str
    jsonld: list[str]
    microdata: list[str]
    videos: dict[str, bool]
    sd: SdSignals | None


def strip_url(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0][:300]


def path_of(url: str) -> str:
    return urlsplit(url).path or "/"


def _skip_path(url: str, words: list[str]) -> bool:
    path = urlsplit(url).path.lower()
    if path.endswith(SKIP_EXTENSIONS):
        return True
    parts = set(tokens(path)) | {p for p in path.split("/") if p}
    return any(w in parts for w in words)


def _from_render(page: RenderedPage) -> _Page:
    return _Page(
        url=page.final_url or page.url,
        internal=list(page.internal_links),
        priority=set(page.priority_links),
        external=dict(page.external_links),
        og=dict(page.og),
        og_source="rendered",
        jsonld=list(page.jsonld),
        microdata=list(page.microdata),
        videos=dict(page.videos),
        sd=page.sd,
    )


def _from_html(url: str, html: str) -> _Page:
    """Faz 1 sunucu HTML'i: render verisi yoksa linkler, her durumda og:image (paylaşım
    botları JS çalıştırmaz) buradan okunur."""
    tree = LexborHTMLParser(html)
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    internal: list[str] = []
    priority: set[str] = set()
    external: dict[str, str] = {}
    videos: dict[str, bool] = {}
    for node in tree.css("a[href], iframe[src]"):
        raw = (node.attributes.get("href") or node.attributes.get("src") or "").strip()
        absolute = urljoin(url, raw)
        if not absolute.startswith(("http://", "https://")):
            continue
        if video_id(absolute):
            videos[absolute] = videos.get(absolute, False) or node.tag == "iframe"
        if node.tag != "a":
            continue
        clean = strip_url(absolute)
        in_chrome = any(p.tag in ("nav", "header", "footer") for p in _parents(node))
        if clean.startswith(origin + "/") or clean == origin:
            internal.append(clean)
            if in_chrome:
                priority.add(clean)
        else:
            external.setdefault(clean, "chrome" if in_chrome else "content")
    og: dict[str, str] = {}
    for tag in OG_TAGS:
        node = tree.css_first(f'meta[property="{tag}"], meta[name="{tag}"]')
        value = (node.attributes.get("content") or "").strip() if node else ""
        if value:
            og[tag] = value[:300]
    jsonld = [n.text() for n in tree.css('script[type="application/ld+json"]')][:10]
    microdata = [n.attributes.get("itemtype") or "" for n in tree.css("[itemtype]")][:20]
    return _Page(
        url=url,
        internal=list(dict.fromkeys(internal)),
        priority=priority,
        external=external,
        og=og,
        og_source="server",
        jsonld=jsonld,
        microdata=[m for m in microdata if m],
        videos=videos,
        sd=None,
    )


def _parents(node: Any) -> list[Any]:
    out = []
    current = node.parent
    while current is not None and len(out) < 30:
        out.append(current)
        current = current.parent
    return out


def insecure_targets(ctx: DeepContext) -> list[str]:
    """Faz 1 `insecure_http_links` bulgusundaki http:// adresleri (host başına bir tane)."""
    seen: dict[str, str] = {}
    for finding in ctx.parent_findings:
        if finding.check_id != INSECURE_LINKS_CHECK:
            continue
        for link in finding.evidence.get("links") or []:
            url = str(link.get("url") or "") if isinstance(link, dict) else ""
            host = (urlsplit(url).hostname or "").lower()
            if host and url.startswith("http://") and host not in seen:
                seen[host] = url
    return list(seen.values())[:MAX_HTTPS_TARGETS]


class LinksModule(DeepModule):
    name = "links"
    lane = Lane.NETWORK

    def __init__(
        self,
        settings: Settings | None = None,
        guard: Any = None,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
        limiter: ServiceLimiter | None = None,
    ) -> None:
        self._settings = settings
        self._guard = guard
        self._transport_factory = transport_factory
        self._limiter = limiter

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    def budget_s(self, settings: Settings) -> float:
        return settings.deep_budget_links_s

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        s = self.settings
        started = time.monotonic()
        deadline = started + self.budget_s(s) - CHECK_BUDGET_S
        wait = min(s.deep_links_render_wait_s, max(0.0, deadline - time.monotonic() - 20))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ctx.render_ready.wait(), timeout=wait)
        pages = self._pages(ctx)
        if not pages:
            raise LinksUnavailableError()
        parts = urlsplit(pages[0].url)
        data = LinksData(site=f"{parts.scheme}://{parts.netloc}")
        data.source = "render" if ctx.render is not None and ctx.render.pages else "phase1"
        ctx.links = data
        async with self._env(parts.hostname or "") as env:
            await self._run_parts(
                data,
                {
                    "internal": self._internal(env, ctx, data, pages),
                    "external": self._external(env, data, pages),
                    "https_targets": self._https_targets(env, ctx, data),
                    "youtube": self._youtube(env, data, pages),
                    "og_image": self._og_images(env, ctx, data, pages),
                    "structured": self._structured(data, pages),
                },
                deadline,
            )
        ctx.meta["links"] = data.summary()
        await ctx.events.emit(
            self.name,
            "deep.event.links_checked",
            internal=data.internal_checked,
            external=data.external_checked,
        )
        skipped = data.skipped_checks()
        if skipped:
            ctx.note_checks("checks_skipped", skipped)
        checks = [
            c
            for c in load_builtin_checks().for_target(TargetType.WEB, 2)
            if c.phase == 2 and c.module == self.name and c.id not in skipped
        ]
        result = await run_checks(ctx, checks)
        out.findings.extend(result.findings)
        if result.failed:
            ctx.note_checks("checks_failed", result.failed)

    # --- girdiler

    def _pages(self, ctx: DeepContext) -> list[_Page]:
        if ctx.render is not None and ctx.render.pages:
            pages = [
                _from_render(p)
                for p in ctx.render.pages
                if p.status is None or p.status < 400 or p is ctx.render.pages[0]
            ]
            return pages
        if ctx.base is not None and ctx.base.pages:
            return [_from_html(p.url, p.html) for p in ctx.base.pages]
        return []

    def _server_pages(self, ctx: DeepContext) -> dict[str, _Page]:
        base: WebContext | None = ctx.base
        if base is None:
            return {}
        return {strip_url(p.url).rstrip("/"): _from_html(p.url, p.html) for p in base.pages}

    # --- parçalar

    async def _run_parts(
        self, data: LinksData, parts: dict[str, Awaitable[str]], deadline: float
    ) -> None:
        async def guarded(name: str, coro: Awaitable[str]) -> None:
            try:
                data.status[name] = await coro
            except Exception as exc:
                logger.warning("link parçası başarısız: %s (%s)", name, type(exc).__name__)
                data.status[name] = PartStatus.UNAVAILABLE

        tasks = [asyncio.create_task(guarded(n, c)) for n, c in parts.items()]
        _, pending = await asyncio.wait(tasks, timeout=max(0.0, deadline - time.monotonic()))
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for name in data.status:
            if data.status[name] == PartStatus.PENDING:
                data.status[name] = PartStatus.TIMEOUT

    async def _internal(
        self, env: "_Env", ctx: DeepContext, data: LinksData, pages: list[_Page]
    ) -> str:
        s = env.settings
        found: dict[str, LinkStatus] = {}
        for page in pages:
            for url in page.internal:
                clean = strip_url(url)
                if not clean.startswith(data.site):
                    continue
                entry = found.setdefault(clean, LinkStatus(clean, LinkOutcome.UNKNOWN))
                path = path_of(page.url)
                if path not in entry.pages and len(entry.pages) < MAX_PAGES_PER_LINK:
                    entry.pages.append(path)
                entry.prominent = entry.prominent or url in page.priority
        data.internal_total = len(found)
        if not found:
            return PartStatus.NO_DATA
        visited = self._visited_statuses(ctx)
        spa_ok = bool(ctx.render and any(p.via_click_ok for p in ctx.render.deep_links))
        data.spa_router_404s_excluded = spa_ok
        todo: list[LinkStatus] = []
        for url, entry in found.items():
            key = url.rstrip("/") or url
            status = visited.get(key)
            if status is not None:
                # Render bu sayfayı zaten açtı: aynı istek tekrar atılmaz
                broken = status in (404, 410) or status >= 500
                entry.status = status
                entry.outcome = LinkOutcome.BROKEN if broken else LinkOutcome.OK
                entry.reason = f"http_{status}" if broken else ""
                data.internal_checked += 1
            elif not _skip_path(url, s.deep_links_skip_path_words):
                todo.append(entry)
        # Menüdeki linkler önce (en çok tıklananlar)
        todo.sort(key=lambda e: (not e.prominent, -len(e.pages)))
        todo = todo[: s.deep_links_max_internal]
        await self._probe_all(env, todo)
        data.internal_checked += len(todo)
        broken = [e for e in found.values() if e.outcome == LinkOutcome.BROKEN]
        if spa_ok:
            # Sunucu SPA rotalarına 404 dönüyor ama sayfa tıklayınca açılıyor: aynı kök neden
            # (render'ın spa_deep_link_404 bulgusu); kırık link olarak tekrar sayılmaz
            broken = [e for e in broken if e.status != 404]
        data.internal = broken
        return PartStatus.OK

    def _visited_statuses(self, ctx: DeepContext) -> dict[str, int]:
        out: dict[str, int] = {}
        if ctx.render is None:
            return out
        for page in ctx.render.pages:
            for url in (page.url, page.final_url):
                if url and page.status is not None:
                    out[strip_url(url).rstrip("/") or url] = page.status
        return out

    async def _probe_all(self, env: "_Env", entries: list[LinkStatus]) -> None:
        sem = asyncio.Semaphore(env.settings.deep_links_concurrency)

        async def one(entry: LinkStatus) -> None:
            async with sem:
                result = await probe_link(env.fetch, env.pacer, entry.url)
            entry.outcome, entry.status, entry.reason = result.outcome, result.status, result.reason

        await asyncio.gather(*(one(e) for e in entries))

    async def _external(self, env: "_Env", data: LinksData, pages: list[_Page]) -> str:
        s = env.settings
        skip = set(s.deep_links_unverifiable_domains)
        found: dict[str, LinkStatus] = {}
        for page in pages:
            for url, where in page.external.items():
                host = (urlsplit(url).hostname or "").lower()
                if not host or registered_domain(host) in skip or host in skip:
                    continue
                if _skip_path(url, s.deep_links_skip_path_words) or video_id(url):
                    continue  # videolar oEmbed ile ayrıca kontrol edilir
                entry = found.setdefault(url, LinkStatus(url, LinkOutcome.UNKNOWN))
                path = path_of(page.url)
                if path not in entry.pages and len(entry.pages) < MAX_PAGES_PER_LINK:
                    entry.pages.append(path)
                entry.prominent = entry.prominent or where == "chrome"
        data.external_total = len(found)
        if not found:
            return PartStatus.NO_DATA
        todo = sorted(found.values(), key=lambda e: (not e.prominent, -len(e.pages)))
        todo = todo[: s.deep_links_max_external]
        await self._probe_all(env, todo)
        data.external_checked = len(todo)
        data.external = [e for e in todo if e.outcome == LinkOutcome.BROKEN]
        return PartStatus.OK

    async def _https_targets(self, env: "_Env", ctx: DeepContext, data: LinksData) -> str:
        targets = insecure_targets(ctx)
        if not targets:
            return PartStatus.NO_DATA

        async def one(url: str) -> HttpsTarget:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            https_url = f"https://{host}{parts.path or '/'}"
            await env.pacer.wait(host)
            try:
                res = await env.fetch(https_url, 0, "HEAD")
            except FetchError as exc:
                if exc.detail in ("tls", "connect"):
                    return HttpsTarget(host, https_url, False, exc.detail)
                return HttpsTarget(host, https_url, None, exc.detail or exc.code)
            # TLS el sıkışması başarılı ve sunucu yanıt verdi: hedef HTTPS destekliyor
            return HttpsTarget(host, https_url, True, f"http_{res.status}")

        sem = asyncio.Semaphore(env.settings.deep_links_concurrency)

        async def limited(url: str) -> HttpsTarget:
            async with sem:
                return await one(url)

        data.https_targets = list(await asyncio.gather(*(limited(u) for u in targets)))
        if all(t.supports_https is None for t in data.https_targets):
            return PartStatus.UNAVAILABLE
        return PartStatus.OK

    async def _youtube(self, env: "_Env", data: LinksData, pages: list[_Page]) -> str:
        s = env.settings
        seen: dict[str, VideoStatus] = {}
        for page in pages:
            for url, embedded in page.videos.items():
                vid = video_id(url)
                if vid is None:
                    continue
                old = seen.get(vid)
                if old is None:
                    seen[vid] = VideoStatus(vid, strip_url(url), path_of(page.url), embedded, "")
                elif embedded and not old.embedded:
                    seen[vid] = VideoStatus(vid, old.url, path_of(page.url), True, "")
        if not seen:
            return PartStatus.NO_DATA
        results: list[VideoStatus] = []
        errors = 0
        limited = False
        for video in list(seen.values())[: s.deep_youtube_max_videos]:
            if not await env.limiter.allow("youtube"):
                limited = True
                break
            try:
                async with asyncio.timeout(s.deep_external_timeouts_s.get("youtube", 10)):
                    res = await env.service.fetch(
                        oembed_url(s.deep_youtube_oembed_endpoint, video.video_id), 64 * 1024
                    )
            except (FetchError, TimeoutError):
                errors += 1
                continue
            if res.status == 429:
                limited = True
                break
            results.append(
                VideoStatus(
                    video.video_id,
                    video.url,
                    video.page,
                    video.embedded,
                    video_state(res.status),
                    res.status,
                )
            )
        data.videos = results
        if results:
            return PartStatus.OK  # kontrol edilemeyen videolar raporda sayılmaz
        if errors:
            logger.info("YouTube oEmbed alınamadı (%d hata)", errors)
        return PartStatus.RATE_LIMITED if limited else PartStatus.UNAVAILABLE

    async def _og_images(
        self, env: "_Env", ctx: DeepContext, data: LinksData, pages: list[_Page]
    ) -> str:
        """Paylaşım botları JS çalıştırmaz: önce sunucu HTML'indeki etiket, yoksa render."""
        s = env.settings
        server = self._server_pages(ctx)
        candidates: list[tuple[str, str, str, str]] = []  # page, tag, raw, source
        seen_urls: set[str] = set()
        for page in pages:
            source_page = server.get(strip_url(page.url).rstrip("/")) or page
            for tag in OG_TAGS:
                raw = source_page.og.get(tag) or page.og.get(tag) or ""
                if not raw:
                    continue
                absolute = urljoin(page.url, raw)
                if absolute in seen_urls:
                    continue
                seen_urls.add(absolute)
                source = source_page.og_source if source_page.og.get(tag) else page.og_source
                candidates.append((page.url, tag, raw, source))
        if not candidates:
            return PartStatus.NO_DATA
        results = []
        for page_url, tag, raw, source in candidates[: s.deep_og_image_max_checks]:
            results.append(await self._og_one(env, page_url, tag, raw, source))
        data.og_images = results
        return PartStatus.OK

    async def _og_one(self, env: "_Env", page_url: str, tag: str, raw: str, source: str) -> OgImage:
        s = env.settings
        url = urljoin(page_url, raw)
        status: int | None = None
        content_type = ""
        size: int | None = None
        dims = None
        if url.startswith(("http://", "https://")):
            host = (urlsplit(url).hostname or "").lower()
            await env.pacer.wait(host)
            try:
                res = await env.fetch(url, s.deep_og_image_max_bytes + 1, "GET")
            except FetchError:
                res = None
            if res is not None:
                status = res.status
                content_type = res.content_type
                declared = res.headers.get("content-length", "")
                size = (
                    int(declared)
                    if declared.isdigit()
                    else (None if res.truncated else len(res.body))
                )
                if res.truncated and size is None:
                    size = s.deep_og_image_max_bytes + 1
                dims = image_size(res.body) if status == 200 else None
        issues = og_issues(
            raw,
            status,
            content_type,
            size,
            dims,
            max_bytes=s.deep_og_image_max_bytes,
            min_px=s.deep_og_image_min_px,
            ratio_min=s.deep_og_image_ratio_min,
            ratio_max=s.deep_og_image_ratio_max,
        )
        return OgImage(
            page=path_of(page_url),
            tag=tag,
            url=strip_url(url),
            source=source,
            status=status,
            content_type=content_type,
            bytes=size,
            width=dims[0] if dims else None,
            height=dims[1] if dims else None,
            issues=issues,
        )

    async def _structured(self, data: LinksData, pages: list[_Page]) -> str:
        analyzed: list[JsonLdPage] = []
        site_types: set[str] = set()
        for page in pages:
            result = analyze_blocks(path_of(page.url), page.jsonld)
            for name in microdata_types(page.microdata):
                if name not in result.types:
                    result.types.append(name)
            analyzed.append(result)
            site_types |= set(result.types)
        data.jsonld = analyzed
        data.present_types = sorted(site_types)
        if all(p.sd is None for p in pages):
            data.status["sd_opportunity"] = PartStatus.UNAVAILABLE  # sinyaller render ister
        else:
            seen_kinds: set[str] = set()
            for page in pages:
                if page.sd is None:
                    continue
                for opp in opportunities(path_of(page.url), page.sd, site_types):
                    if opp.kind not in seen_kinds:
                        seen_kinds.add(opp.kind)
                        data.opportunities.append(opp)
            data.status["sd_opportunity"] = PartStatus.OK
        return PartStatus.OK if any(p.blocks for p in analyzed) else PartStatus.NO_DATA

    # --- altyapı

    @contextlib.asynccontextmanager
    async def _env(self, own_host: str) -> AsyncIterator["_Env"]:
        from launchcheck.security.ssrf import UrlGuard

        s = self.settings
        guard = self._guard or UrlGuard(timeout_s=s.dns_timeout_s)
        probe_settings = s.model_copy(
            update={
                "http_request_timeout_s": s.deep_links_request_timeout_s,
                "max_total_bytes": FETCH_TOTAL_BYTES,
            }
        )
        transport = self._transport_factory() if self._transport_factory else None
        fetcher = SafeFetcher(probe_settings, guard, transport)
        service_transport = self._transport_factory() if self._transport_factory else None
        service = SafeFetcher(probe_settings, guard, service_transport)
        redis_client: Any = None
        limiter = self._limiter
        if limiter is None:
            try:
                from redis.asyncio import Redis

                redis_client = Redis.from_url(s.redis_url)
                limiter = RedisServiceLimiter(redis_client, s.deep_external_rate_limits)
            except Exception:  # pragma: no cover - Redis yoksa sınırsız (servis hatası yakalanır)
                limiter = NoLimit()

        async def fetch(url: str, max_bytes: int, method: str) -> FetchResult:
            return await fetcher.fetch(url, max_bytes, True, method=method)

        try:
            yield _Env(
                settings=s,
                fetch=fetch,
                service=service,
                limiter=limiter,
                pacer=HostPacer(
                    s.deep_links_per_host_interval_s,
                    own_host=own_host.lower(),
                    own_interval_s=s.deep_links_own_host_interval_s,
                ),
            )
        finally:
            await fetcher.aclose()
            await service.aclose()
            if redis_client is not None:
                await redis_client.aclose()


@dataclass
class _Env:
    settings: Settings
    fetch: Callable[[str, int, str], Awaitable[FetchResult]]
    service: SafeFetcher
    limiter: ServiceLimiter
    pacer: HostPacer
