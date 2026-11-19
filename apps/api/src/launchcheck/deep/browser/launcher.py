"""Güvenli Chromium oturumu (docs/PHASE2.md §12.1-12.2).

- Tüm trafik `EgressProxy`'den geçer: `--proxy-server` + `--proxy-bypass-list=<-loopback>`
  (Chromium'un varsayılan "localhost'u proxy'siz aç" istisnası da kaldırılır).
- Chromium kendisi hiçbir host'u çözemez (`--host-resolver-rules=MAP * ~NOTFOUND`): proxy'yi
  atlayan bir yol olsa bile DNS çözümlemesi başarısız olur. Proxy adresi literal IP'dir.
- QUIC/HTTP3 kapalı (UDP ile proxy atlanmasın), WebRTC proxy dışı UDP kullanamaz.
- İndirme yok, service worker yok, izin istekleri reddedilir, dialoglar otomatik kapanır,
  pop-up/yeni sekmeler kapatılır. Her tarama yeni ve boş bir context; `finally` ile kapanır.

Sandbox kararı (docs/decisions.md, P2): Chromium `--no-sandbox` ile, container izolasyonuna
güvenerek çalışır (root olmayan kullanıcı, `cap_drop: ALL`, `no-new-privileges`, salt okunur
kök dosya sistemi, ağ çıkışı sadece egress proxy üzerinden).
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from launchcheck.config import Settings
from launchcheck.deep.browser.egress_proxy import EgressProxy
from launchcheck.security.ssrf import UrlGuard

if TYPE_CHECKING:  # playwright sadece worker-deep imajında (opsiyonel "deep" bağımlılığı)
    from playwright.async_api import Browser, BrowserContext, Dialog, Page, Playwright

logger = logging.getLogger(__name__)

MOBILE_VIEWPORT = {"width": 375, "height": 812}
DESKTOP_VIEWPORT = {"width": 1440, "height": 900}


def chromium_args(proxy_port: int) -> list[str]:
    return [
        f"--proxy-server=http://127.0.0.1:{proxy_port}",
        "--proxy-bypass-list=<-loopback>",
        # Proxy adresi (literal 127.0.0.1) kuraldan hariç; başka hiçbir ad çözülemez
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-sync",
        "--no-first-run",
        "--no-default-browser-check",
        "--dns-prefetch-disable",
        "--disable-features=DnsOverHttps,AsyncDns,NetworkPrediction,MediaRouter,OptimizationHints",
    ]


@dataclass(frozen=True)
class BrowserLimits:
    page_timeout_s: float = 20.0
    max_total_bytes: int = 200 * 1024 * 1024

    @classmethod
    def from_settings(cls, settings: "Settings") -> "BrowserLimits":
        return cls(settings.deep_page_timeout_s, settings.deep_proxy_max_total_bytes)


class BrowserSession:
    """`async with BrowserSession.open(...) as session:` — proxy + Chromium + tek context."""

    def __init__(
        self,
        proxy: EgressProxy,
        playwright: "Playwright",
        browser: "Browser",
        context: "BrowserContext",
        limits: BrowserLimits,
    ) -> None:
        self.proxy = proxy
        self._playwright = playwright
        self._browser = browser
        self.context = context
        self.limits = limits
        self._main: Page | None = None

    @classmethod
    @contextlib.asynccontextmanager
    async def open(
        cls,
        guard: UrlGuard,
        user_agent: str,
        limits: BrowserLimits | None = None,
        allow: frozenset[tuple[str, int]] = frozenset(),
        viewport: dict[str, int] | None = None,
        is_mobile: bool = False,
    ) -> AsyncIterator["BrowserSession"]:
        from playwright.async_api import async_playwright

        limits = limits or BrowserLimits()
        proxy = EgressProxy(guard, limits.max_total_bytes, allow=allow)
        await proxy.start()
        playwright = await async_playwright().start()
        browser = context = None
        try:
            browser = await playwright.chromium.launch(
                headless=True, args=chromium_args(proxy.port), downloads_path=None
            )
            context = await browser.new_context(**context_options(user_agent, viewport, is_mobile))
            session = cls(proxy, playwright, browser, context, limits)
            context.on("page", session._on_page)
            context.set_default_timeout(limits.page_timeout_s * 1000)
            context.set_default_navigation_timeout(limits.page_timeout_s * 1000)
            yield session
        finally:
            for closer in (
                context.close if context else None,
                browser.close if browser else None,
                playwright.stop,
                proxy.close,
            ):
                if closer is not None:
                    with contextlib.suppress(Exception):
                        await closer()

    async def new_page(self) -> "Page":
        """Taramanın ana sekmesi. Sayfanın açtığı pop-up'lar ve yeni sekmeler kapatılır."""
        page = await self.context.new_page()
        self._main = page
        page.on("dialog", _dismiss)
        return page

    def _on_page(self, page: "Page") -> None:
        if self._main is not None and page is not self._main:
            _spawn(_close_quietly(page))
        else:
            page.on("dialog", _dismiss)


def context_options(
    user_agent: str, viewport: dict[str, int] | None, is_mobile: bool
) -> dict[str, Any]:
    return {
        "user_agent": user_agent,
        "viewport": viewport or DESKTOP_VIEWPORT,
        "is_mobile": is_mobile,
        "has_touch": is_mobile,
        "accept_downloads": False,
        "service_workers": "block",
        "permissions": [],  # kamera, mikrofon, konum, bildirim istekleri reddedilir
        "java_script_enabled": True,
        "ignore_https_errors": False,
        "bypass_csp": False,
        "locale": "tr-TR",
    }


# Olay geri çağrılarından başlatılan görevler: referans tutulmazsa çöp toplayıcı silebilir
_BACKGROUND: set[asyncio.Task[None]] = set()


def _spawn(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.ensure_future(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


def _dismiss(dialog: "Dialog") -> None:
    _spawn(_dismiss_quietly(dialog))


async def _dismiss_quietly(dialog: "Dialog") -> None:
    with contextlib.suppress(Exception):
        await dialog.dismiss()


async def _close_quietly(page: "Page") -> None:
    with contextlib.suppress(Exception):
        await page.close()
