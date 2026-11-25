"""Gizlilik ve tedarik zinciri toplaması gerçek tarayıcıda (PHASE2 §6.5).

1. Yeni, boş bir context'te ana sayfa açılır; hiçbir şeye tıklamadan `observe_s` boyunca ağ
   istekleri ve yazılan çerezler kaydedilir ("onaysız" aşama).
2. Çerez banner'ı aranır; varsa "Reddet / Sadece gerekli" düğmesine tıklanır (gerekirse önce
   "Ayarlar"). Takipçi çerezleri silinir (onay tercihi çerezi kalır), sayfa yenilenir ve kayıt
   tekrarlanır ("reddettikten sonra" aşaması). Yeniden çıkan takipçi çerezi gerçekten yazılmıştır.
3. axe-core ana sayfa ve kilit sayfalarda, mobil ve masaüstünde çalıştırılır.

Bütün sayfa yüklemelerinde script adresleri ve (sınırlar içinde) gövdeleri sürüm tespiti için
toplanır. Kayıtlarda sorgu dizesi, çerez değeri ve istek gövdesi tutulmaz.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from launchcheck.checks.shared.domains import registered_domain
from launchcheck.config import Settings
from launchcheck.deep.browser import consent
from launchcheck.deep.browser.launcher import BrowserSession
from launchcheck.deep.privacy_data import ConsentInfo, RejectState
from launchcheck.deep.supply.axe import RUN_JS, run_options
from launchcheck.deep.supply.trackers import TrackerDb

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Request, Response

logger = logging.getLogger(__name__)

AXE_SETTLE_S = 1.5
BODY_TIMEOUT_S = 5
GA_DOMAINS = frozenset({"google-analytics.com", "analytics.google.com"})
GOOGLE_DOMAINS = frozenset(
    {*GA_DOMAINS, "googletagmanager.com", "doubleclick.net", "googleadservices.com", "google.com"}
)


class PrivacyPageError(Exception):
    """Ana sayfa açılamadı."""


@dataclass(frozen=True)
class RequestRecord:
    url: str  # sorgu dizesiz
    host: str
    resource_type: str
    consent_denied: bool  # Google Consent Mode: bu istek için izin verilmemiş (gcs=G1x0 vb.)


@dataclass(frozen=True)
class CookieRecord:
    name: str
    domain: str


@dataclass
class PrivacyRaw:
    consent: ConsentInfo
    before_requests: list[RequestRecord]
    before_cookies: list[CookieRecord]
    after_requests: list[RequestRecord] | None = None
    after_cookies: list[CookieRecord] | None = None
    scripts: dict[str, str | None] = field(default_factory=dict)  # url → gövde (sınırlı)
    axe_runs: list[tuple[str, str, list[dict[str, Any]]]] = field(default_factory=list)


@dataclass
class PrivacyInput:
    home_url: str
    axe_urls: list[str]  # ana sayfa dahil
    axe_source: str | None
    tracker_db: TrackerDb
    deadline: float | None = None
    progress: Callable[[str], Awaitable[None]] | None = None

    def time_left(self, reserve_s: float = 0) -> bool:
        return self.deadline is None or time.monotonic() + reserve_s < self.deadline


def consent_denied(url: str) -> bool:
    """Google Consent Mode `gcs=G1<ad><analytics>`: bu isteğin ait olduğu depolama izni yok
    (çerezsiz ölçüm "ping"i). Değer sadece burada okunur, saklanmaz."""
    parts = urlsplit(url)
    domain = registered_domain((parts.hostname or "").lower())
    if domain not in GOOGLE_DOMAINS or "gcs=" not in parts.query:
        return False
    gcs = (parse_qs(parts.query).get("gcs") or [""])[0]
    if len(gcs) < 4 or not gcs.startswith("G1"):
        return False
    return gcs[3] == "0" if domain in GA_DOMAINS else gcs[2] == "0"


def _strip(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0][:300]


class ScriptStore:
    """Sürüm tespiti için script gövdeleri: dosya sayısı, dosya başı ve toplam bayt sınırlı."""

    def __init__(self, max_scripts: int, max_bytes: int, max_total: int) -> None:
        self.bodies: dict[str, str | None] = {}
        self._max_scripts = max_scripts
        self._max_bytes = max_bytes
        self._left = max_total
        self._tasks: set[asyncio.Task[None]] = set()

    def seen(self, url: str) -> None:
        self.bodies.setdefault(url, None)

    def wants(self, url: str) -> bool:
        captured = sum(1 for b in self.bodies.values() if b is not None)
        return self.bodies.get(url) is None and captured < self._max_scripts and self._left > 0

    def capture(self, url: str, response: "Response") -> None:
        task = asyncio.ensure_future(self._read(url, response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read(self, url: str, response: "Response") -> None:
        try:
            async with asyncio.timeout(BODY_TIMEOUT_S):
                body = await response.body()
        except Exception:
            return
        if not body or len(body) > self._max_bytes or len(body) > self._left:
            return
        self._left -= len(body)
        self.bodies[url] = body.decode("utf-8", "replace")

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


class TrafficRecorder:
    def __init__(self, page: "Page", scripts: ScriptStore) -> None:
        self.requests: list[RequestRecord] = []
        self._scripts = scripts
        page.on("request", self._on_request)
        page.on("response", self._on_response)

    def _on_request(self, request: "Request") -> None:
        url = request.url
        if not url.startswith(("http://", "https://")):
            return
        host = (urlsplit(url).hostname or "").lower()
        self.requests.append(
            RequestRecord(_strip(url), host, request.resource_type, consent_denied(url))
        )
        if request.resource_type == "script":
            self._scripts.seen(_strip(url))

    def _on_response(self, response: "Response") -> None:
        if response.request.resource_type != "script" or response.status != 200:
            return
        url = _strip(response.url)
        if self._scripts.wants(url):
            self._scripts.capture(url, response)

    def take(self) -> list[RequestRecord]:
        out, self.requests = self.requests, []
        return out


async def cookies_of(context: "BrowserContext") -> list[CookieRecord]:
    return [
        CookieRecord(str(c.get("name", ""))[:100], str(c.get("domain", "")).lstrip("."))
        for c in await context.cookies()
    ]


async def clear_tracker_cookies(
    context: "BrowserContext", cookies: list[CookieRecord], db: TrackerDb, site: str
) -> None:
    """Onay tercihini saklayan çerez kalsın: sadece takipçi çerezleri silinir."""
    for cookie in cookies:
        tracker = db.cookie_tracker(cookie.name)
        if tracker is None and registered_domain(cookie.domain) != site:
            matched = db.match(cookie.domain)
            tracker = matched if matched is not None and matched.tracking else None
        if tracker is None:
            continue
        with contextlib.suppress(Exception):
            await context.clear_cookies(name=cookie.name, domain=cookie.domain)
        with contextlib.suppress(Exception):
            await context.clear_cookies(name=cookie.name, domain="." + cookie.domain)


class PrivacyCollector:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def collect(self, session: BrowserSession, data: PrivacyInput) -> PrivacyRaw:
        s = self._s
        store = ScriptStore(
            s.deep_supply_max_scripts, s.deep_supply_max_script_bytes, s.deep_supply_max_total_bytes
        )
        context = session.context
        site = registered_domain(urlsplit(data.home_url).hostname or "")
        page = await session.new_page(context)
        recorder = TrafficRecorder(page, store)
        try:
            try:
                await page.goto(data.home_url, wait_until="load")
            except Exception as exc:
                raise PrivacyPageError() from exc
            await asyncio.sleep(s.deep_privacy_observe_s)  # onaysız gözlem penceresi (ölçüm)
            raw = PrivacyRaw(
                consent=await consent.detect(page),
                before_requests=recorder.take(),
                before_cookies=await cookies_of(context),
            )
            raw.consent.cmp = raw.consent.cmp or consent.cmp_from_scripts(list(store.bodies))
            if data.progress:
                await data.progress("consent")
            await consent.reject(page, raw.consent)
            if raw.consent.reject in (RejectState.CLICKED, RejectState.CLICKED_SECOND_LAYER):
                await self._after_reject(page, context, recorder, raw, data, site)
            if data.axe_source and data.time_left(10):
                raw.axe_runs.append(await self._axe_current(page, data, "desktop"))
        finally:
            await page.close()
        if data.axe_source:
            raw.axe_runs += await self._axe_pages(session, data, store)
        await store.drain()
        raw.scripts = dict(store.bodies)
        return raw

    async def _after_reject(
        self,
        page: "Page",
        context: "BrowserContext",
        recorder: TrafficRecorder,
        raw: PrivacyRaw,
        data: PrivacyInput,
        site: str,
    ) -> None:
        s = self._s
        await asyncio.sleep(s.deep_privacy_after_click_s)  # tercih kaydı/yönlendirme
        await clear_tracker_cookies(context, await cookies_of(context), data.tracker_db, site)
        recorder.take()  # tıklama anındaki istekler iki aşamaya da yazılmaz
        try:
            await page.reload(wait_until="load")
        except Exception:
            raw.consent.reject = RejectState.FAILED
            return
        await asyncio.sleep(s.deep_privacy_observe_s)
        raw.after_requests = recorder.take()
        raw.after_cookies = await cookies_of(context)
        raw.consent.banner_after_reject = await consent.banner_visible(page)

    async def _axe_current(
        self, page: "Page", data: PrivacyInput, viewport: str
    ) -> tuple[str, str, list[dict[str, Any]]]:
        path = urlsplit(page.url).path or "/"
        return path, viewport, await self._run_axe(page, data)

    async def _run_axe(self, page: "Page", data: PrivacyInput) -> list[dict[str, Any]]:
        try:
            async with asyncio.timeout(self._s.deep_axe_timeout_s):
                await page.evaluate(data.axe_source or "")
                result = await page.evaluate(RUN_JS, run_options())
        except Exception as exc:
            logger.info("axe çalışmadı (%s)", type(exc).__name__)
            return []
        return [r for r in result or [] if isinstance(r, dict)]

    async def _axe_pages(
        self, session: BrowserSession, data: PrivacyInput, store: ScriptStore
    ) -> list[tuple[str, str, list[dict[str, Any]]]]:
        """Ana sayfa mobilde + kilit sayfalar mobil ve masaüstünde (ana sayfa masaüstü zaten
        "reddettikten sonra" hâliyle ölçüldü)."""
        mobile = await session.new_context(mobile=True)
        jobs: list[tuple[str, BrowserContext, str]] = [(data.axe_urls[0], mobile, "mobile")]
        for url in data.axe_urls[1:]:
            jobs += [(url, session.context, "desktop"), (url, mobile, "mobile")]
        sem = asyncio.Semaphore(2)
        out: list[tuple[str, str, list[dict[str, Any]]]] = []

        async def one(url: str, context: "BrowserContext", viewport: str) -> None:
            async with sem:
                if not data.time_left(self._s.deep_axe_timeout_s):
                    return
                page = await session.new_page(context)
                TrafficRecorder(page, store)
                try:
                    await page.goto(url, wait_until="load")
                    await asyncio.sleep(AXE_SETTLE_S)  # geç çizilen içerik (ölçüm penceresi)
                    out.append(await self._axe_current(page, data, viewport))
                except Exception as exc:
                    logger.info("axe sayfası açılamadı (%s)", type(exc).__name__)
                finally:
                    await page.close()

        await asyncio.gather(*(one(*job) for job in jobs))
        return out
