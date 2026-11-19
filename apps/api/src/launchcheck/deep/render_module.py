"""Render modülü (tarayıcı şeridi): Faz 1 bağlamını yeniden toplar, siteyi gerçek tarayıcıda
gezer, ekran görüntülerini kaydeder ve `module = "render"` olan Faz 2 check'lerini çalıştırır."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.collectors.base import CollectorError
from launchcheck.collectors.web_collector import WebCollector, WebContext
from launchcheck.config import Settings, get_settings
from launchcheck.db.models import ScanAsset
from launchcheck.deep.browser.errors import BotChallengeError
from launchcheck.deep.context import DeepContext
from launchcheck.deep.modules import DeepModule, Lane, ModuleOutput
from launchcheck.deep.render_data import RenderData
from launchcheck.security.ssrf import UrlGuard

logger = logging.getLogger(__name__)
SOURCEMAP_CHECK = "web.files.sourcemap_exposed"
BLOCKED_CODE = "site_blocked_bot"


class SiteBlockedError(Exception):
    """Site taramayı bot doğrulamasıyla engelledi (render modülü başarısız sayılır)."""


MAX_BUNDLES = 60
COLLECT_BUDGET_SHARE = 0.7


def sourcemap_urls(ctx: DeepContext) -> list[str]:
    for finding in ctx.parent_findings:
        if finding.check_id == SOURCEMAP_CHECK:
            return [str(u) for u in finding.evidence.get("files") or []]
    return []


class RenderModule(DeepModule):
    name = "render"
    lane = Lane.BROWSER

    def __init__(
        self,
        settings: Settings | None = None,
        guard: UrlGuard | None = None,
        # Testler için: (bağlam, girdi) → RenderData. Varsayılan gerçek Chromium.
        collect: Callable[[DeepContext, WebContext | None, Any], Awaitable[RenderData]]
        | None = None,
        # Testler için: hedef URL → Faz 1 WebContext. Varsayılan WebCollector (SSRF korumalı).
        collect_base: Callable[[str], Awaitable[WebContext]] | None = None,
    ) -> None:
        self._settings = settings
        self._guard = guard
        self._collect = collect
        self._collect_base = collect_base

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    def budget_s(self, settings: Settings) -> float:
        return settings.deep_budget_browser_s

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        base = await self._base(ctx)
        ctx.base = base
        try:
            ctx.render = await (self._collect or self._browser_collect)(ctx, base, out)
        except BotChallengeError as exc:
            # Doğrulama sayfasını taramak yanıltıcı bulgular üretir: modül başarısız sayılır ve
            # neden rapora yazılır (check'ler "geçti" listesine de girmez).
            ctx.meta["deep_blocked"] = BLOCKED_CODE
            await ctx.events.emit(self.name, "deep.event.site_blocked")
            raise SiteBlockedError(BLOCKED_CODE) from exc
        checks = [
            c
            for c in load_builtin_checks().for_target(TargetType.WEB, 2)
            if c.phase == 2 and c.module == self.name
        ]
        result = await run_checks(ctx, checks)
        out.findings.extend(result.findings)
        if result.failed:
            ctx.meta["checks_failed"] = [
                *ctx.parent_meta.get("checks_failed", []),
                *result.failed,
            ]

    async def _base(self, ctx: DeepContext) -> WebContext | None:
        """Faz 1 bağlamı (sunucu HTML'i, JS bundle'ları). Site art arda isteklere bot doğrulaması
        gösterebilir; toplanamazsa render tarayıcıdan devam eder."""
        if self._collect_base is not None:
            return await self._collect_base(ctx.target_url)
        settings = self.settings
        guard = self._guard or UrlGuard(timeout_s=settings.dns_timeout_s)
        try:
            collected = await WebCollector(settings, guard).collect(
                ctx.scan_id, {"url": ctx.target_url}
            )
        except CollectorError as exc:
            logger.info("Faz 1 bağlamı toplanamadı (%s); tarayıcıyla devam", exc.code)
            ctx.meta["deep_base_error"] = exc.code
            return None
        return collected if isinstance(collected, WebContext) else None

    async def _browser_collect(
        self, ctx: DeepContext, base: WebContext | None, out: ModuleOutput
    ) -> RenderData:
        from launchcheck.deep.browser.launcher import BrowserLimits, BrowserSession
        from launchcheck.deep.browser.render import RenderCollector, RenderInput

        settings = self.settings
        guard = self._guard or UrlGuard(timeout_s=settings.dns_timeout_s)
        last_emit = 0.0

        async def progress(done: int, total: int) -> None:
            nonlocal last_emit
            if time.monotonic() - last_emit > 3 or done == total:  # olay akışını boğma
                last_emit = time.monotonic()
                await ctx.events.emit(self.name, "deep.event.page_rendered", done=done, total=total)

        def save(kind: str, url: str, data: bytes, width: int, height: int) -> ScanAsset | None:
            if ctx.assets is None:
                return None
            asset = ctx.assets.save(ctx.scan_id, kind, url, data, width, height)
            out.assets.append(asset)
            return asset

        async def fetch(url: str) -> bytes | None:
            res = await base.fetch_url(url) if base is not None else None  # SSRF korumalı
            return res.body if res is not None and res.status == 200 else None

        data = RenderInput(
            home_url=base.final_url if base else ctx.target_url.rstrip("/") + "/",
            seed_urls=[p.url for p in base.pages] if base else [],
            baseline_path=base._baseline_path if base else f"/{uuid.uuid4().hex}.txt",
            bundle_texts=[s.content for s in base.scripts if not s.inline][:MAX_BUNDLES]
            if base
            else [],
            sourcemap_urls=sourcemap_urls(ctx) if base else [],
            fetch=fetch,
            save_screenshot=save,
            progress=progress,
            # Bütçenin bir kısmı check'lere ve kapanışa kalsın
            deadline=time.monotonic() + self.budget_s(settings) * COLLECT_BUDGET_SHARE,
        )
        limits = BrowserLimits.from_settings(settings)
        async with BrowserSession.open(guard, settings.bot_user_agent, limits) as session:
            return await RenderCollector(settings).collect(session, data)
