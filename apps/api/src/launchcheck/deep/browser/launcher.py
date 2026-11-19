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
DESKTOP_UA_BASE = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari/537.36"
)
MOBILE_UA_BASE = (
    "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome Mobile Safari/537.36"
)


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
    """`async with BrowserSession.open(...) as session:` — proxy + Chromium. Varsayılan bir
    context (`session.context`) açılır; `new_context()` ile aynı tarayıcı ve proxy üzerinde
    ek context'ler (mobil, JS kapalı) açılabilir. Hepsi `finally` ile kapanır."""

    def __init__(
        self,
        proxy: EgressProxy,
        playwright: "Playwright",
        browser: "Browser",
        limits: BrowserLimits,
        user_agent: str,
    ) -> None:
        self.proxy = proxy
        self._playwright = playwright
        self._browser = browser
        self.limits = limits
        self.user_agent = user_agent
        self.context: BrowserContext
        self._contexts: list[BrowserContext] = []
        self._ours: set[Page] = set()
        self._creating = 0

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
        session: BrowserSession | None = None
        browser = None
        try:
            browser = await playwright.chromium.launch(
                headless=True, args=chromium_args(proxy.port), downloads_path=None
            )
            session = cls(proxy, playwright, browser, limits, user_agent)
            session.context = await session.new_context(
                mobile=is_mobile, viewport=viewport, user_agent=user_agent
            )
            yield session
        finally:
            closers = [c.close for c in (session._contexts if session else [])]
            closers += [browser.close] if browser else []
            for closer in [*closers, playwright.stop, proxy.close]:
                with contextlib.suppress(Exception):
                    await closer()

    async def new_context(
        self,
        *,
        mobile: bool = False,
        javascript: bool = True,
        viewport: dict[str, int] | None = None,
        user_agent: str | None = None,
    ) -> "BrowserContext":
        options = context_options(
            user_agent or browser_user_agent(self.user_agent, mobile),
            viewport or (MOBILE_VIEWPORT if mobile else DESKTOP_VIEWPORT),
            mobile,
        )
        options["java_script_enabled"] = javascript
        context = await self._browser.new_context(**options)
        context.on("page", self._on_page)
        context.set_default_timeout(self.limits.page_timeout_s * 1000)
        context.set_default_navigation_timeout(self.limits.page_timeout_s * 1000)
        self._contexts.append(context)
        return context

    async def new_page(self, context: "BrowserContext | None" = None) -> "Page":
        """Taramanın açtığı sekme. Sayfanın kendisinin açtığı pop-up/yeni sekmeler kapatılır."""
        self._creating += 1
        try:
            page = await (context or self.context).new_page()
        finally:
            self._creating -= 1
        self._ours.add(page)
        page.on("dialog", _dismiss)
        page.on("close", self._forget)
        return page

    def _forget(self, page: "Page") -> None:
        self._ours.discard(page)

    def _on_page(self, page: "Page") -> None:
        if self._creating > 0 or page in self._ours:
            return  # new_page() ile açılıyor; dialog dinleyicisini new_page ekler
        _spawn(_close_quietly(page))


def browser_user_agent(bot_user_agent: str, mobile: bool) -> str:
    """Gerçek tarayıcı UA'sı + bot kimliği: siteler mobil/masaüstü içeriği doğru sunar,
    biz yine de kendimizi tanıtırız (PHASE2 §12.4)."""
    base = MOBILE_UA_BASE if mobile else DESKTOP_UA_BASE
    return f"{base} {bot_user_agent}"


def context_options(
    user_agent: str, viewport: dict[str, int] | None, is_mobile: bool
) -> dict[str, Any]:
    return {
        "user_agent": user_agent,
        "viewport": viewport or (MOBILE_VIEWPORT if is_mobile else DESKTOP_VIEWPORT),
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
