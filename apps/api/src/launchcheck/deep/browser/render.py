"""Gerçek ziyaretçi gezintisi (PHASE2 §6.1): sayfaları mobil ve masaüstünde açar, ölçer.

Toplanan her şey `RenderData`'ya yazılır; check'ler tarayıcıya dokunmaz. Sadece normal bir
ziyaretçinin yaptığı istekler atılır: sayfa açmak, link tıklamak. Form doldurulmaz/gönderilmez.
"""

import asyncio
import contextlib
import io
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.domains import registered_domain
from launchcheck.checks.shared.text import tokens
from launchcheck.checks.web.conversion import auth_target
from launchcheck.config import Settings
from launchcheck.db.models import ScanAsset
from launchcheck.deep.browser.egress_proxy import BLOCKED_HEADER
from launchcheck.deep.browser.errors import BotChallengeError
from launchcheck.deep.browser.launcher import BrowserSession
from launchcheck.deep.browser.viewport import (
    MEASURE_JS,
    ROUTES_JS,
    STORAGE_JS,
    parse_broken_images,
    parse_junk,
    parse_metrics,
    parse_storage,
)
from launchcheck.deep.render_data import (
    AdminProbe,
    ConsoleError,
    CookieInfo,
    DeepLinkProbe,
    NetworkFailure,
    NotFoundProbe,
    OfflineProbe,
    RenderData,
    RenderedPage,
    SdSignals,
)

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, ConsoleMessage, Page, Request, Response

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 200
# Kaynak yüklenemedi mesajları başarısız istek olarak ayrıca sayılır; JS hatası değildir
RESOURCE_ERROR_RE = re.compile(r"^Failed to load resource|net::ERR_", re.IGNORECASE)
CORS_RE = re.compile(r"blocked by CORS policy|CORS", re.IGNORECASE)
NORMALIZE_RE = re.compile(r"https?://\S+|\b\d+\b")
IGNORED_FAILURES = ("net::ERR_ABORTED", "net::ERR_TUNNEL_CONNECTION_FAILED", "net::ERR_BLOCKED")
LOGIN_PATH_RE = re.compile(r"login|signin|sign-in|giris|oturum|auth", re.IGNORECASE)
ADMIN_ROUTE_RE = re.compile(
    r"[\"'`](/(?:admin|yonetim|panel|backoffice|dashboard/admin|cms)(?:/[a-z0-9_-]+){0,2})/?[\"'`]",
    re.IGNORECASE,
)
DEFAULT_404_MARKERS = {
    "nginx": re.compile(r"404 Not Found\s*nginx", re.IGNORECASE),
    "apache": re.compile(r"The requested URL was not found on this server", re.IGNORECASE),
    "express": re.compile(r"Cannot GET /", re.IGNORECASE),
    "vercel": re.compile(r"404: NOT_FOUND", re.IGNORECASE),
    "netlify": re.compile(r"Looks like you've followed a broken link", re.IGNORECASE),
    "iis": re.compile(r"Server Error in '/' Application|HTTP Error 404", re.IGNORECASE),
}
OFFLINE_MESSAGE_RE = re.compile(
    r"offline|çevrimdışı|cevrimdisi|bağlantı|baglanti|connection|internet|tekrar dene|"
    r"retry|try again|error|hata",
    re.IGNORECASE,
)
CLICK_PATH_JS = """(path) => { const a = [...document.querySelectorAll('a[href]')]
  .find((x) => new URL(x.href, location.href).pathname === path);
  if (a) a.click(); }"""
OFFLINE_PROBES = 2
OFFLINE_WAIT_MS = 4000
MIN_PAGE_WORDS = 5
# Küçük = daha kötü
OFFLINE_RANK = {"blank": 0, "spinner": 1, "message": 2, "not_spa": 3, "browser_error_page": 4}
SPINNER_JS = (
    "() => !!document.querySelector('[aria-busy=true], [role=progressbar], "
    "[class*=spinner], [class*=loader], [class*=loading]')"
)
SOURCES_RE = re.compile(rb'"sources"\s*:\s*\[(.*?)\]', re.DOTALL)
SOURCE_ITEM_RE = re.compile(rb'"((?:[^"\\]|\\.)*)"')
PRODUCT_PATH_RE = re.compile(r"\b(product|products|urun|urunler|shop|magaza|features|ozellikler)\b")
HOME_LINK_JS = """() => [...document.querySelectorAll('a[href]')].some((a) => {
  const u = new URL(a.href, location.href);
  return u.origin === location.origin && (u.pathname === '/' || u.pathname === '');
})"""
MAX_SOURCEMAP_PATHS = 50
MAX_SOURCEMAPS = 5
WEBP_QUALITY = 70


@dataclass
class RenderInput:
    """Render modülünün dış bağımlılıkları (testlerde değiştirilebilir)."""

    home_url: str
    seed_urls: list[str]  # Faz 1'in taradığı sayfalar
    baseline_path: str  # Faz 1'in rastgele yolu (404 sayfası için)
    bundle_texts: list[str]  # Faz 1 JS bundle'ları (admin rota adayları)
    sourcemap_urls: list[str] = field(default_factory=list)
    fetch: Callable[[str], Awaitable[bytes | None]] | None = None
    save_screenshot: Callable[[str, str, bytes, int, int], ScanAsset | None] | None = None
    progress: Callable[[int, int], Awaitable[None]] | None = None
    # Kilit sayfalar belli olunca (hız şeridi onları Lighthouse ile ölçer)
    on_key_pages: Callable[[list[str]], None] | None = None
    # time.monotonic() sınırı: geçince yeni sayfa açılmaz, toplananlarla devam edilir
    deadline: float | None = None

    def time_left(self) -> bool:
        return self.deadline is None or time.monotonic() < self.deadline


class PageRecorder:
    """Bir sekmedeki console hataları, yakalanmamış istisnalar ve başarısız istekler.
    Ayrıca "load + N sn sessizlik" beklemesi için ağ etkinliğini izler."""

    def __init__(self, page: "Page") -> None:
        self.console: list[ConsoleError] = []
        self.failures: list[NetworkFailure] = []
        self._cors_origins: set[str] = set()
        self._inflight = 0
        self._last_activity = time.monotonic()
        page.on("console", self._on_console)
        page.on("pageerror", self._on_error)
        page.on("request", self._on_request)
        page.on("requestfinished", self._on_done)
        page.on("requestfailed", self._on_failed)
        page.on("response", self._on_response)

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    def _on_request(self, request: "Request") -> None:
        self._inflight += 1
        self._touch()

    def _on_done(self, request: "Request") -> None:
        self._inflight = max(0, self._inflight - 1)
        self._touch()

    def _on_console(self, message: "ConsoleMessage") -> None:
        if message.type != "error":
            return
        text = message.text or ""
        if CORS_RE.search(text):
            for url in re.findall(r"https?://[^\s'\"]+", text):
                self._cors_origins.add(_origin(url))
            return
        if RESOURCE_ERROR_RE.search(text):
            return
        self.console.append(ConsoleError(text[:MAX_MESSAGE_CHARS], "console"))

    def _on_error(self, error: Any) -> None:
        message = str(getattr(error, "message", "") or error)
        self.console.append(ConsoleError(message[:MAX_MESSAGE_CHARS], "exception"))

    def _on_response(self, response: "Response") -> None:
        if response.status < 400 or response.request.resource_type == "document":
            return
        if BLOCKED_HEADER.lower() in {k.lower() for k in response.headers}:
            return  # bizim proxy'mizin reddi (iç adres vb.), sitenin hatası değil
        url = _strip_query(response.url)
        if urlsplit(url).path.endswith("/favicon.ico"):
            return  # Faz 1 favicon check'inin konusu
        error = "http_5xx" if response.status >= 500 else "http_4xx"
        self.failures.append(
            NetworkFailure(url, response.request.resource_type, response.status, error)
        )

    def _on_failed(self, request: "Request") -> None:
        self._inflight = max(0, self._inflight - 1)
        self._touch()
        failure = request.failure or ""
        if any(f in failure for f in IGNORED_FAILURES):
            return
        url = _strip_query(request.url)
        cors = request.resource_type in ("fetch", "xhr") and _origin(url) in self._cors_origins
        self.failures.append(
            NetworkFailure(url, request.resource_type, None, "cors" if cors else "network")
        )

    async def settle(self, quiet_s: float, max_wait_s: float) -> None:
        """`load` sonrası: ağ `quiet_s` sessiz kalana veya `max_wait_s` dolana kadar bekle."""
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline:
            if self._inflight == 0 and time.monotonic() - self._last_activity >= quiet_s:
                return
            await asyncio.sleep(0.2)

    def mark_cors(self) -> None:
        """CORS mesajı istek hatasından sonra gelebilir: sonradan yeniden sınıflandır."""
        self.failures = [
            NetworkFailure(f.url, f.resource_type, f.status, "cors")
            if f.error == "network" and _origin(f.url) in self._cors_origins
            else f
            for f in self.failures
        ]


def _strip_query(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0][:300]


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _norm(url: str) -> str:
    return _strip_query(url).rstrip("/") or url


def dedupe_console(errors: list[ConsoleError]) -> list[ConsoleError]:
    seen: set[str] = set()
    out = []
    for error in errors:
        key = NORMALIZE_RE.sub("#", error.message)
        if key not in seen:
            seen.add(key)
            out.append(error)
    return out


def page_role(url: str, link_text: str = "") -> str | None:
    """Kilit sayfa türü: pricing > signup > product (sıralama için)."""
    path_words = " ".join(tokens(urlsplit(url).path))
    if keywords.has("pricing_pages", path_words) or keywords.has("pricing_pages", link_text):
        return "pricing"
    if auth_target(url) == "register":
        return "signup"
    if PRODUCT_PATH_RE.search(path_words):
        return "product"
    return None


def plan_pages(
    home: str, seeds: list[str], priority: list[str], others: list[str], limit: int
) -> list[str]:
    """Ana sayfa + Faz 1 sayfaları + menü linkleri + fiyat/kayıt/ürün + diğer iç linkler."""
    roles = [u for u in [*priority, *others] if page_role(u)]
    ordered = [home, *seeds, *priority, *roles, *others]
    out: list[str] = []
    seen: set[str] = set()
    for url in ordered:
        key = _norm(url)
        if key in seen or urlsplit(url).path.lower().endswith((".pdf", ".zip", ".xml", ".json")):
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def key_pages(urls: list[str], priority: list[str], count: int) -> list[str]:
    """Fiyat > kayıt > ürün > ilk menü linki."""
    rank = {"pricing": 0, "signup": 1, "product": 2}
    candidates = sorted(
        (u for u in urls[1:] if page_role(u)), key=lambda u: rank[page_role(u) or "product"]
    )
    for url in priority:
        if url in urls[1:] and url not in candidates:
            candidates.append(url)
    return candidates[:count]


def admin_candidates(bundles: list[str], home: str, limit: int) -> list[str]:
    found: list[str] = []
    for text in bundles:
        for match in ADMIN_ROUTE_RE.finditer(text):
            path = match.group(1).lower()
            if path not in found:
                found.append(path)
    return [urljoin(home, p) for p in found[:limit]]


def parse_sourcemap_sources(body: bytes) -> list[str]:
    """Kesilmiş (1 MB) map dosyalarında da çalışır: sadece `sources` dizisi okunur."""
    match = SOURCES_RE.search(body)
    if not match:
        return []
    out = []
    for item in SOURCE_ITEM_RE.findall(match.group(1)):
        path = item.decode("utf-8", "replace").replace("\\/", "/")
        path = re.sub(r"^(?:webpack://[^/]*/|\.\./|\./)+", "", path)
        if path and not path.startswith("node_modules/") and "/node_modules/" not in path:
            out.append(path[:200])
    return out


class RenderCollector:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def collect(self, session: BrowserSession, data: RenderInput) -> RenderData:
        s = self._s
        out = RenderData()
        desktop = session.context
        mobile = await session.new_context(mobile=True)

        home = await self._render(session, desktop, mobile, data.home_url)
        if home.bot_challenge:
            raise BotChallengeError()
        out.pages.append(home)
        urls = plan_pages(
            home.final_url or data.home_url,
            data.seed_urls,
            home.priority_links,
            home.internal_links,
            s.deep_max_pages,
        )
        ordered_keys = key_pages(urls, home.priority_links, s.deep_key_pages)
        keys = set(ordered_keys)
        if data.on_key_pages:
            data.on_key_pages(ordered_keys)
        await self._extras_for_key(session, desktop, mobile, home, data, is_home=True)

        sem = asyncio.Semaphore(s.deep_render_concurrency)
        done = 1

        async def one(url: str) -> RenderedPage | None:
            nonlocal done
            async with sem:
                if not data.time_left():
                    return None
                try:
                    page = await self._render(session, desktop, mobile, url)
                    if url in keys and data.time_left():
                        await self._extras_for_key(session, desktop, mobile, page, data, False)
                except Exception as exc:
                    logger.info("sayfa render edilemedi (%s)", type(exc).__name__)
                    page = None
                done += 1
                if data.progress:
                    await data.progress(done, len(urls))
                return page

        rendered = await asyncio.gather(*(one(u) for u in urls[1:]))
        out.pages += [p for p in rendered if p is not None]

        if data.time_left():
            out.deep_links = await self._deep_links(session, desktop, out, home)
            out.not_found = await self._not_found(session, desktop, home, data.baseline_path)
            out.offline = await self._offline(session, home)
            out.admin = await self._admin(session, desktop, data, home)
        out.storage, out.cookies = await self._storage_and_cookies(session, desktop, home)
        if data.fetch:
            out.sourcemap_sources, out.sourcemap_source_count = await self._sourcemaps(data)
        out.blocked_hosts = [(b.host, b.port, b.reason) for b in session.proxy.stats.blocked]
        return out

    # --- tek sayfa

    async def _open(
        self, session: BrowserSession, context: "BrowserContext", url: str
    ) -> tuple["Page", PageRecorder, int | None]:
        page = await session.new_page(context)
        recorder = PageRecorder(page)
        status = None
        with contextlib.suppress(Exception):
            response = await page.goto(url, wait_until="load")
            status = response.status if response else None
        await recorder.settle(self._s.deep_render_quiet_s, self._s.deep_render_max_wait_s)
        recorder.mark_cors()
        return page, recorder, status

    async def _measure(self, page: "Page", mobile: bool, full: bool) -> dict[str, Any]:
        try:
            result = await page.evaluate(MEASURE_JS, {"mobile": mobile, "full": full})
        except Exception as exc:
            logger.info("ölçüm yapılamadı (%s)", type(exc).__name__)
            return {}
        return result if isinstance(result, dict) else {}

    async def _render(
        self,
        session: BrowserSession,
        desktop: "BrowserContext",
        mobile: "BrowserContext",
        url: str,
    ) -> RenderedPage:
        (d_page, d_rec, status), (m_page, m_rec, _) = await asyncio.gather(
            self._open(session, desktop, url), self._open(session, mobile, url)
        )
        try:
            d_raw, m_raw = await asyncio.gather(
                self._measure(d_page, mobile=False, full=True),
                self._measure(m_page, mobile=True, full=False),
            )
            vd, vm = d_page.viewport_size or {}, m_page.viewport_size or {}
            page = RenderedPage(
                url=url,
                final_url=d_page.url,
                status=status,
                title=str(d_raw.get("title", "")),
                h1=str(d_raw.get("h1", "")),
                canonical=str(d_raw.get("canonical", "")),
                og={str(k): str(v) for k, v in (d_raw.get("og") or {}).items()},
                main_words=int(d_raw.get("mainWords") or 0),
                internal_links=[str(u) for u in d_raw.get("links") or []],
                priority_links=[str(u) for u in d_raw.get("priorityLinks") or []],
                external_links={
                    str(e.get("url")): str(e.get("where") or "content")
                    for e in d_raw.get("externalLinks") or []
                    if isinstance(e, dict) and e.get("url")
                },
                console_errors=dedupe_console(d_rec.console + m_rec.console),
                failures=list(dict.fromkeys(d_rec.failures + m_rec.failures)),
                broken_images=parse_broken_images(d_raw),
                junk=parse_junk(d_raw),
                desktop=parse_metrics(d_raw, vd.get("width", 1440), vd.get("height", 900)),
                mobile=parse_metrics(m_raw, vm.get("width", 375), vm.get("height", 812)),
                is_funnel=page_role(url) in ("pricing", "signup"),
                has_password_field=bool(d_raw.get("hasPassword")),
                admin_signals=[str(a) for a in d_raw.get("admin") or []],
                bot_challenge=bool(d_raw.get("challenge")),
                jsonld=[str(b) for b in d_raw.get("jsonld") or []][:10],
                microdata=[str(m) for m in d_raw.get("microdata") or []][:20],
                videos={
                    str(v.get("url")): bool(v.get("embed"))
                    for v in (d_raw.get("videos") or [])[:30]
                    if isinstance(v, dict) and v.get("url")
                },
                sd=SdSignals.from_js(d_raw.get("sd")),
            )
            page.widths[page.mobile.width] = page.mobile
            page.widths[page.desktop.width] = page.desktop
            return page
        finally:
            await asyncio.gather(d_page.close(), m_page.close(), return_exceptions=True)

    async def _extras_for_key(
        self,
        session: BrowserSession,
        desktop: "BrowserContext",
        mobile: "BrowserContext",
        page: RenderedPage,
        data: RenderInput,
        is_home: bool,
    ) -> None:
        """Kilit sayfalar: ek genişliklerde ölçüm + mobil/masaüstü/JS kapalı ekran görüntüsü."""
        m_page, _, _ = await self._open(session, mobile, page.url)
        try:
            await self._screenshot(m_page, page.url, "screenshot_mobile", data)
            for width in self._s.deep_measure_widths:
                if width in page.widths:
                    continue
                height = 812 if width < 768 else 1024 if width < 1440 else 900
                await m_page.set_viewport_size({"width": width, "height": height})
                await asyncio.sleep(0.3)
                raw = await self._measure(m_page, mobile=width < 1024, full=False)
                page.widths[width] = parse_metrics(raw, width, height)
        finally:
            await m_page.close()
        d_page, _, _ = await self._open(session, desktop, page.url)
        try:
            await self._screenshot(d_page, page.url, "screenshot_desktop", data)
        finally:
            await d_page.close()
        nojs = await session.new_context(mobile=False, javascript=False)
        n_page, _, _ = await self._open(session, nojs, page.url)
        try:
            await self._screenshot(n_page, page.url, "screenshot_nojs", data)
        finally:
            await n_page.close()
            await nojs.close()

    async def _screenshot(self, page: "Page", url: str, kind: str, data: RenderInput) -> None:
        if data.save_screenshot is None:
            return
        try:
            png = await page.screenshot(type="png", full_page=False)
            webp, width, height = to_webp(png)
            data.save_screenshot(kind, url, webp, width, height)
        except Exception as exc:
            logger.info("ekran görüntüsü alınamadı (%s)", type(exc).__name__)

    # --- ek yoklamalar

    async def _deep_links(
        self,
        session: BrowserSession,
        desktop: "BrowserContext",
        data: RenderData,
        home: RenderedPage,
    ) -> list[DeepLinkProbe]:
        """Doğrudan açınca 404 olan iç sayfa, ana sayfadan tıklayınca açılıyor mu?"""
        missing = [p for p in data.pages[1:] if p.status == 404 and p.url in home.internal_links]
        probes = []
        for target in missing[:5]:
            page = await session.new_page(desktop)
            try:
                await page.goto(home.final_url or home.url, wait_until="load")
                path = urlsplit(target.url).path
                clicked = await page.evaluate(
                    """(path) => { const a = [...document.querySelectorAll('a[href]')]
                       .find((x) => new URL(x.href, location.href).pathname === path);
                       if (!a) return false; a.click(); return true; }""",
                    path,
                )
                ok = False
                if clicked:
                    await page.wait_for_timeout(2500)
                    raw = await self._measure(page, mobile=False, full=True)
                    ok = urlsplit(page.url).path == path and int(raw.get("mainWords") or 0) >= 10
                probes.append(DeepLinkProbe(target.url, target.status, ok))
            except Exception as exc:
                logger.info("derin link yoklanamadı (%s)", type(exc).__name__)
            finally:
                await page.close()
        return probes

    async def _not_found(
        self, session: BrowserSession, desktop: "BrowserContext", home: RenderedPage, path: str
    ) -> NotFoundProbe | None:
        url = urljoin(home.final_url or home.url, path)
        page, _, status = await self._open(session, desktop, url)
        try:
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            has_home = await page.evaluate(HOME_LINK_JS)
            default = next((n for n, rx in DEFAULT_404_MARKERS.items() if rx.search(text)), None)
            return NotFoundProbe(path, status, len(str(text).split()), bool(has_home), default)
        except Exception:
            return None
        finally:
            await page.close()

    async def _offline(self, session: BrowserSession, home: RenderedPage) -> OfflineProbe | None:
        """Yüklendikten sonra bağlantı kopunca: boş ekran / sonsuz yükleme / hata mesajı?
        Bazı rotalar ağ isteği atmadan çizilir; en fazla `OFFLINE_PROBES` menü linki denenir ve
        en kötü sonuç döner."""
        home_key = _norm(home.final_url or home.url)
        targets = [u for u in dict.fromkeys(home.priority_links) if _norm(u) != home_key]
        worst: OfflineProbe | None = None
        for target in targets[:OFFLINE_PROBES]:
            probe = await self._offline_one(session, home, target)
            if probe is None:
                continue
            if worst is None or OFFLINE_RANK[probe.outcome] < OFFLINE_RANK[worst.outcome]:
                worst = probe
            if worst.outcome in ("blank", "spinner"):
                break
        return worst

    async def _offline_one(
        self, session: BrowserSession, home: RenderedPage, target: str
    ) -> OfflineProbe | None:
        context = await session.new_context()
        page, _, _ = await self._open(session, context, home.final_url or home.url)
        try:
            await context.set_offline(True)
            path = urlsplit(target).path
            await page.evaluate(CLICK_PATH_JS, path)
            await page.wait_for_timeout(OFFLINE_WAIT_MS)
            if page.url.startswith("chrome-error://"):
                # klasik site: tarayıcının kendi çevrimdışı sayfası (sitenin sorunu değil)
                return OfflineProbe(target, "browser_error_page", 0)
            text = str(await page.evaluate("() => document.body ? document.body.innerText : ''"))
            words = len(text.split())
            if words < MIN_PAGE_WORDS:
                outcome = "blank"
            elif OFFLINE_MESSAGE_RE.search(text):
                outcome = "message"
            elif await page.evaluate(SPINNER_JS):
                outcome = "spinner"
            else:
                outcome = "not_spa"  # sayfa yerinde kaldı / içerik ağsız çizildi
            return OfflineProbe(target, outcome, words)
        except Exception:
            return None
        finally:
            with contextlib.suppress(Exception):
                await context.set_offline(False)
            await page.close()
            await context.close()

    async def _admin(
        self,
        session: BrowserSession,
        desktop: "BrowserContext",
        data: RenderInput,
        home: RenderedPage,
    ) -> list[AdminProbe]:
        """JS'te tanımlı yönetim rotaları: sadece açılır, hiçbir şeye tıklanmaz."""
        out = []
        for url in await self._admin_urls(session, desktop, data, home):
            page, _, status = await self._open(session, desktop, url)
            try:
                raw = await self._measure(page, mobile=False, full=True)
                out.append(
                    AdminProbe(
                        path=urlsplit(url).path,
                        status=status,
                        signals=[str(s) for s in raw.get("admin") or []],
                        login_form=bool(raw.get("hasPassword")),
                        redirected_to_login=bool(LOGIN_PATH_RE.search(urlsplit(page.url).path)),
                    )
                )
            finally:
                await page.close()
        return out

    async def _admin_urls(
        self,
        session: BrowserSession,
        desktop: "BrowserContext",
        data: RenderInput,
        home: RenderedPage,
    ) -> list[str]:
        """Faz 1 bundle'ları + tarayıcının yüklediği aynı origin JS'lerdeki rota adayları."""
        limit = self._s.deep_admin_route_probes
        urls = admin_candidates(data.bundle_texts, home.final_url, limit)
        page = await session.new_page(desktop)
        try:
            await page.goto(home.final_url or home.url, wait_until="load")
            paths = await page.evaluate(
                ROUTES_JS, {"maxFiles": 30, "maxBytes": 5_000_000, "maxRoutes": limit}
            )
            for path in paths or []:
                url = urljoin(home.final_url, str(path))
                if url not in urls:
                    urls.append(url)
        except Exception as exc:
            logger.info("rota adayları okunamadı (%s)", type(exc).__name__)
        finally:
            await page.close()
        return urls[:limit]

    async def _storage_and_cookies(
        self, session: BrowserSession, desktop: "BrowserContext", home: RenderedPage
    ) -> tuple[list[Any], list[CookieInfo]]:
        page, _, _ = await self._open(session, desktop, home.final_url or home.url)
        try:
            storage = parse_storage(await page.evaluate(STORAGE_JS))
        except Exception:
            storage = []
        finally:
            await page.close()
        site = registered_domain(urlsplit(home.final_url or home.url).hostname or "")
        cookies = []
        for c in await desktop.cookies():
            domain = str(c.get("domain", "")).lstrip(".")
            if registered_domain(domain) != site:
                continue  # üçüncü taraf çerezleri sitenin ayarı değil
            cookies.append(
                CookieInfo(
                    name=str(c.get("name", ""))[:100],
                    domain=domain,
                    secure=bool(c.get("secure")),
                    http_only=bool(c.get("httpOnly")),
                    same_site=str(c.get("sameSite", "")),
                    session=float(c.get("expires", -1) or -1) < 0,
                )
            )
        return storage, cookies

    async def _sourcemaps(self, data: RenderInput) -> tuple[list[str], int]:
        paths: list[str] = []
        for url in data.sourcemap_urls[:MAX_SOURCEMAPS]:
            body = await data.fetch(url) if data.fetch else None
            for path in parse_sourcemap_sources(body or b""):
                if path not in paths:
                    paths.append(path)
        return paths[:MAX_SOURCEMAP_PATHS], len(paths)


def to_webp(png: bytes, max_width: int = 1440) -> tuple[bytes, int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        image.load()
        if image.width > max_width:
            ratio = max_width / image.width
            image = image.resize((max_width, round(image.height * ratio)))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "WEBP", quality=WEBP_QUALITY, method=4)
        return buffer.getvalue(), image.width, image.height
