"""Kıyas için site taraması gerçek tarayıcıda (PHASE2 §7.3, §7.5).

Kullanıcının sitesi ve örnekler AYNI yöntemle taranır: mobil ana sayfa (anatomi + fiyat incelemesi
+ ekran görüntüsü), masaüstü ekran görüntüsü, fiyat sayfası ve kayıt sayfası; site başına en fazla
`max_pages` sayfa. Her adres açılmadan önce robots.txt'e bakılır. Hiçbir güvenlik check'i
çalışmaz, hiçbir şeye tıklanmaz, form doldurulmaz/gönderilmez; sadece sayfalar açılır.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from launchcheck.checks.web.conversion import auth_target
from launchcheck.deep.browser.funnel import FUNNEL_JS, skip_patterns
from launchcheck.deep.browser.launcher import BrowserSession
from launchcheck.deep.browser.render import page_role, to_webp
from launchcheck.deep.browser.viewport import _script

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

logger = logging.getLogger(__name__)

ANATOMY_JS = _script("anatomy.js")
LOAD_TIMEOUT_MS = 15000
IDLE_TIMEOUT_MS = 2500


@dataclass
class SiteCrawl:
    origin: str
    ok: bool = False
    reason: str = ""  # robots_blocked | unreachable | bot_challenge
    home: dict[str, Any] = field(default_factory=dict)  # anatomy.js
    home_inspect: dict[str, Any] = field(default_factory=dict)  # funnel.js (fiyatlar)
    pricing_url: str | None = None
    pricing_inspect: dict[str, Any] | None = None
    signup_url: str | None = None
    signup_inspect: dict[str, Any] | None = None
    pages: int = 0
    # tür → (webp, genişlik, yükseklik)
    shots: dict[str, tuple[bytes, int, int]] = field(default_factory=dict)


Allowed = Callable[[str], bool]


class SiteCrawler:
    def __init__(self, max_pages: int) -> None:
        self._max_pages = max_pages

    async def crawl(self, session: BrowserSession, origin: str, allowed: Allowed) -> SiteCrawl:
        result = SiteCrawl(origin=origin.rstrip("/"))
        home_url = result.origin + "/"
        if not allowed(home_url):
            result.reason = "robots_blocked"
            return result
        mobile = await session.new_context(mobile=True)
        try:
            await self._home(session, mobile, home_url, result)
            if not result.ok:
                return result
            await self._desktop_shot(session, home_url, result)
            await self._subpages(session, mobile, result, allowed)
        finally:
            with contextlib.suppress(Exception):
                await mobile.close()
        return result

    async def _open(self, page: "Page", url: str) -> bool:
        try:
            response = await page.goto(url, wait_until="load", timeout=LOAD_TIMEOUT_MS)
        except Exception as exc:
            logger.info("kıyas sayfası açılamadı (%s)", type(exc).__name__)
            return False
        if response is not None and response.status >= 400:
            return False
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
        return True

    async def _home(
        self, session: BrowserSession, context: "BrowserContext", url: str, result: SiteCrawl
    ) -> None:
        page = await session.new_page(context)
        try:
            if not await self._open(page, url):
                result.reason = "unreachable"
                return
            result.pages += 1
            challenge = await page.evaluate(
                "() => /just a moment|attention required|security checkpoint|verify you are human/i"
                ".test(document.title || '')"
            )
            if challenge:
                result.reason = "bot_challenge"
                return
            result.home = await page.evaluate(ANATOMY_JS) or {}
            result.home_inspect = await page.evaluate(FUNNEL_JS, {"mode": "inspect"}) or {}
            cta = await page.evaluate(FUNNEL_JS, {"mode": "cta", "skipLabels": skip_patterns()})
            result.home["cta_href"] = str(((cta or {}).get("cta") or {}).get("href") or "")
            await self._shot(page, "mobile", result)
            result.ok = True
        except Exception as exc:
            logger.info("kıyas ana sayfası incelenemedi (%s)", type(exc).__name__)
            result.reason = result.reason or "unreachable"
        finally:
            await page.close()

    async def _desktop_shot(self, session: BrowserSession, url: str, result: SiteCrawl) -> None:
        context = await session.new_context(mobile=False)
        try:
            page = await session.new_page(context)
            if await self._open(page, url):
                await self._shot(page, "desktop", result)
            await page.close()
        finally:
            with contextlib.suppress(Exception):
                await context.close()

    async def _subpages(
        self,
        session: BrowserSession,
        context: "BrowserContext",
        result: SiteCrawl,
        allowed: Allowed,
    ) -> None:
        links = [str(u) for u in result.home.get("links") or [] if str(u).startswith("http")]
        pricing = next((u for u in links if page_role(u) == "pricing"), None)
        signup = next((u for u in links if auth_target(u) == "register"), None)
        cta = str(result.home.get("cta_href") or "")
        if signup is None and cta.startswith(result.origin):
            signup = cta
        for kind, url in (("pricing", pricing), ("signup", signup)):
            if url is None or result.pages >= self._max_pages or not allowed(url):
                continue
            page = await session.new_page(context)
            try:
                if await self._open(page, url):
                    result.pages += 1
                    inspect = await page.evaluate(FUNNEL_JS, {"mode": "inspect"}) or {}
                    if kind == "pricing":
                        result.pricing_url, result.pricing_inspect = url, inspect
                    else:
                        result.signup_url, result.signup_inspect = page.url, inspect
            except Exception as exc:
                logger.info("kıyas alt sayfası incelenemedi (%s)", type(exc).__name__)
            finally:
                await page.close()

    async def _shot(self, page: "Page", kind: str, result: SiteCrawl) -> None:
        try:
            png = await page.screenshot(type="png", full_page=False)
            result.shots[kind] = await asyncio.to_thread(to_webp, png)
        except Exception as exc:
            logger.info("kıyas ekran görüntüsü alınamadı (%s)", type(exc).__name__)
