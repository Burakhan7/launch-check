"""Huni yürüyüşü gerçek tarayıcıda, mobil görünümde (PHASE2 §6.6).

Ana sayfa → birincil CTA (tıklanır) → [giriş sayfasıysa kayıt linki] → kayıt formu; ayrıca fiyat
sayfası. Her adımda tık sayısı, yüklenme süresi, adres ve mobil ekran görüntüsü.

Pasiflik: hiçbir alana yazılmaz, form gönderilmez. CTA tıklaması sadece form dışındaki,
`submit` olmayan ve durum değiştirmeyen (sepete ekle, satın al, çıkış... `funnel.skip_cta`)
öğelere yapılır; diğer adımlar link adresini açmaktır.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from launchcheck.checks.shared import keywords
from launchcheck.checks.web.conversion import auth_target
from launchcheck.config import Settings
from launchcheck.db.models import ScanAsset
from launchcheck.deep.browser.launcher import BrowserSession
from launchcheck.deep.browser.render import to_webp
from launchcheck.deep.browser.viewport import _script

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

logger = logging.getLogger(__name__)

FUNNEL_JS = _script("funnel.js")
CLICK_TIMEOUT_MS = 3000
LOAD_TIMEOUT_MS = 10000
IDLE_TIMEOUT_MS = 2000
NAV_TIMING_JS = """() => { const n = performance.getEntriesByType('navigation')[0];
  return n && n.loadEventEnd > 0 ? Math.round(n.loadEventEnd - n.startTime) : null; }"""


@dataclass
class RawStep:
    kind: str  # home | cta | login | signup | pricing
    url: str
    clicks: int
    load_ms: int | None
    via: str
    inspect: dict[str, Any] = field(default_factory=dict)
    asset: ScanAsset | None = None


@dataclass
class FunnelRaw:
    steps: list[RawStep] = field(default_factory=list)
    cta_text: str = ""


@dataclass
class FunnelInput:
    home_url: str
    # (adres, ana sayfadan tık) → sırayla denenecek kayıt / fiyat sayfası adayları
    signup_candidates: list[tuple[str, int]]
    pricing_candidates: list[tuple[str, int]]
    save_screenshot: Callable[[str, str, bytes, int, int], ScanAsset | None] | None = None
    deadline: float | None = None

    def time_left(self, reserve_s: float = 5) -> bool:
        return self.deadline is None or time.monotonic() + reserve_s < self.deadline


def skip_patterns() -> list[str]:
    """CTA olarak tıklanmayacak etiketler (JS'e regex kaynağı olarak gider)."""
    out = []
    for item in keywords.words("funnel.skip_cta"):
        word = _js_escape(item.rstrip("*"))
        tail = "" if item.endswith("*") else "(?![a-z0-9])"
        out.append(f"(?<![a-z0-9]){word}{tail}")  # etiketin herhangi bir yerinde, kelime olarak
    return out


def _js_escape(text: str) -> str:
    return "".join("\\" + c if c in r".*+?^${}()|[]\/" else c for c in text)


class FunnelCollector:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def collect(self, session: BrowserSession, data: FunnelInput) -> FunnelRaw:
        raw = FunnelRaw()
        context = await session.new_context(mobile=True)
        try:
            await self._walk(session, context, data, raw)
        finally:
            with contextlib.suppress(Exception):
                await context.close()
        return raw

    async def _walk(
        self, session: BrowserSession, context: "BrowserContext", data: FunnelInput, raw: FunnelRaw
    ) -> None:
        page = await session.new_page(context)
        try:
            home = await self._open(page, data.home_url)
            if home is None:
                return
            raw.steps.append(await self._step(page, "home", 0, home, "direct", data))
            signup_found = await self._via_cta(page, data, raw)
        finally:
            await page.close()
        if not signup_found:
            for url, clicks in data.signup_candidates:
                if not data.time_left():
                    break
                if await self._visit(session, context, url, "signup", clicks, data, raw):
                    break
        for url, clicks in data.pricing_candidates[:1]:
            if data.time_left():
                await self._visit(session, context, url, "pricing", clicks, data, raw)

    async def _via_cta(self, page: "Page", data: FunnelInput, raw: FunnelRaw) -> bool:
        """CTA'ya tıklar; açılan sayfada kayıt formu yoksa ve bir giriş sayfasıysa kayıt
        linkine geçer. Kayıt formu bulunursa True."""
        from launchcheck.deep.funnel.analysis import pick_signup

        try:
            found = await page.evaluate(FUNNEL_JS, {"mode": "cta", "skipLabels": skip_patterns()})
        except Exception as exc:
            logger.info("CTA bulunamadı (%s)", type(exc).__name__)
            return False
        cta = (found or {}).get("cta")
        if not cta or not data.time_left():
            return False
        raw.cta_text = str(cta.get("text") or "")[:80]
        load = await self._click(page, str(cta.get("href") or ""))
        if load is None:
            return False
        step = await self._step(page, "cta", 1, load, "cta", data)
        raw.steps.append(step)
        if pick_signup(page.url, step.inspect) is not None:
            step.kind = "signup"
            return True
        # Giriş sayfası → kayıt linki (bir tık daha)
        register = next(
            (u for u in step.inspect.get("links") or [] if auth_target(str(u)) == "register"),
            None,
        )
        if auth_target(page.url) == "login" or register:
            step.kind = "login"
        if register and data.time_left():
            opened = await self._open(page, str(register))
            if opened is not None:
                signup = await self._step(page, "signup", 2, opened, "link", data)
                raw.steps.append(signup)
                return pick_signup(page.url, signup.inspect) is not None
        return False

    async def _visit(
        self,
        session: BrowserSession,
        context: "BrowserContext",
        url: str,
        kind: str,
        clicks: int,
        data: FunnelInput,
        raw: FunnelRaw,
    ) -> bool:
        from launchcheck.deep.funnel.analysis import pick_signup

        page = await session.new_page(context)
        try:
            load = await self._open(page, url)
            if load is None:
                return False
            step = await self._step(page, kind, clicks, load, "link", data)
            raw.steps.append(step)
            return kind != "signup" or pick_signup(page.url, step.inspect) is not None
        finally:
            await page.close()

    # --- sayfa işlemleri

    async def _open(self, page: "Page", url: str) -> int | None:
        """Adresi açar; yüklenme süresi (ms, Navigation Timing) veya açılamazsa None."""
        started = time.monotonic()
        try:
            response = await page.goto(url, wait_until="load", timeout=LOAD_TIMEOUT_MS)
        except Exception as exc:
            logger.info("huni adımı açılamadı (%s)", type(exc).__name__)
            return None
        if response is not None and response.status >= 400:
            return None
        wall = int((time.monotonic() - started) * 1000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
        return await self._nav_timing(page) or wall

    async def _click(self, page: "Page", href: str) -> int | None:
        """CTA tıklaması: tam sayfa geçişinde Navigation Timing, SPA geçişinde tıklamadan
        `load`a kadar geçen süre. Tıklanamazsa (üstünde banner vb.) link adresi açılır."""
        origin = await self._time_origin(page)
        started = time.monotonic()
        try:
            await page.locator('[data-lc-cta="1"]').first.click(timeout=CLICK_TIMEOUT_MS)
        except Exception:
            return await self._open(page, href) if href else None
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("load", timeout=LOAD_TIMEOUT_MS)
        wall = int((time.monotonic() - started) * 1000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
        if await self._time_origin(page) != origin:
            return await self._nav_timing(page) or wall
        return wall

    async def _time_origin(self, page: "Page") -> float | None:
        with contextlib.suppress(Exception):
            return float(await page.evaluate("() => performance.timeOrigin"))
        return None

    async def _nav_timing(self, page: "Page") -> int | None:
        with contextlib.suppress(Exception):
            value = await page.evaluate(NAV_TIMING_JS)
            return int(value) if value is not None else None
        return None

    async def _step(
        self, page: "Page", kind: str, clicks: int, load_ms: int, via: str, data: FunnelInput
    ) -> RawStep:
        inspect: dict[str, Any] = {}
        try:
            result = await page.evaluate(FUNNEL_JS, {"mode": "inspect"})
            inspect = result if isinstance(result, dict) else {}
        except Exception as exc:
            logger.info("huni sayfası incelenemedi (%s)", type(exc).__name__)
        step = RawStep(kind, page.url.split("?", 1)[0], clicks, load_ms, via, inspect)
        if data.save_screenshot is not None:
            try:
                png = await page.screenshot(type="png", full_page=False)
                webp, width, height = await asyncio.to_thread(to_webp, png)
                step.asset = data.save_screenshot("funnel_step", step.url, webp, width, height)
            except Exception as exc:
                logger.info("huni ekran görüntüsü alınamadı (%s)", type(exc).__name__)
        return step
