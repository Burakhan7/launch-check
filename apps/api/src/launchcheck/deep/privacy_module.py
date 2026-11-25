"""Gizlilik ve tedarik zinciri modülü (tarayıcı şeridi, render'dan sonra; PHASE2 §6.5).

Parçalar ve check'leri:
- consent: onaysız yüklenen takipçiler, onay mekanizması eksikliği
- reject: "Reddet"ten sonra bile çalışan takipçiler (reddetme düğmesi bulunamazsa atlanır)
- libraries: retire.js veritabanıyla açıklı kütüphane sürümleri (veritabanı yoksa atlanır)
- scripts: ele geçirilmiş/terk edilmiş CDN'lerden yüklenen script'ler
- axe: axe-core erişilebilirlik ihlalleri (axe dosyası yoksa atlanır)

Site bot doğrulamasıyla engellediyse (render modülü) bu modül çalışmaz.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.checks.shared.domains import registered_domain
from launchcheck.config import Settings, get_settings
from launchcheck.deep.browser.privacy import PrivacyInput, PrivacyRaw
from launchcheck.deep.context import DeepContext
from launchcheck.deep.modules import DeepModule, Lane, ModuleOutput
from launchcheck.deep.privacy_data import PartStatus, PrivacyData, RejectState
from launchcheck.deep.supply.analysis import (
    classify_phase,
    load_current_retire,
    risky_scripts,
    scan_libraries,
)
from launchcheck.deep.supply.axe import group_violations, load_axe_source
from launchcheck.deep.supply.trackers import load_tracker_db
from launchcheck.security.ssrf import UrlGuard

logger = logging.getLogger(__name__)

CHECK_BUDGET_S = 8
CLICKED = (RejectState.CLICKED, RejectState.CLICKED_SECOND_LAYER)


class PrivacyBlockedError(Exception):
    """Site bot doğrulaması gösterdi (render modülü): gizlilik ölçümü yanıltıcı olurdu."""


def axe_targets(ctx: DeepContext, home: str, count: int) -> list[str]:
    """Ana sayfa + kilit sayfalar (fiyat > kayıt > ürün) + menü linkleri, en fazla `count`."""
    urls = [home]
    candidates = list(ctx.key_urls)
    if ctx.render is not None and ctx.render.home is not None:
        ok = {p.url for p in ctx.render.pages if p.status is not None and p.status < 400}
        candidates += [u for u in ctx.render.home.priority_links if u in ok]
    home_key = home.rstrip("/")
    for url in candidates:
        if url.rstrip("/") != home_key and url not in urls:
            urls.append(url)
        if len(urls) >= count:
            break
    return urls


class PrivacyModule(DeepModule):
    name = "privacy"
    lane = Lane.BROWSER

    def __init__(
        self,
        settings: Settings | None = None,
        guard: UrlGuard | None = None,
        # Testler için: (bağlam, girdi) → PrivacyRaw. Varsayılan gerçek Chromium.
        collect: Callable[[DeepContext, PrivacyInput], Awaitable[PrivacyRaw]] | None = None,
    ) -> None:
        self._settings = settings
        self._guard = guard
        self._collect = collect

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    def budget_s(self, settings: Settings) -> float:
        return settings.deep_budget_privacy_s

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        s = self.settings
        if ctx.meta.get("deep_blocked"):
            raise PrivacyBlockedError()
        home = (
            ctx.render.home.final_url or ctx.render.home.url
            if ctx.render is not None and ctx.render.home is not None
            else ctx.target_url.rstrip("/") + "/"
        )
        site = registered_domain(urlsplit(home).hostname or "")
        parts = urlsplit(home)
        data = PrivacyData(site=f"{parts.scheme}://{parts.netloc}")
        ctx.privacy = data
        db = load_tracker_db(s.deep_tracker_list_path)
        axe_source = load_axe_source(s.deep_axe_path)

        async def progress(step: str) -> None:
            await ctx.events.emit(self.name, "deep.event.privacy_consent_checked")

        data_in = PrivacyInput(
            home_url=home,
            axe_urls=axe_targets(ctx, home, s.deep_axe_pages),
            axe_source=axe_source,
            tracker_db=db,
            deadline=time.monotonic() + self.budget_s(s) - CHECK_BUDGET_S,
            progress=progress,
        )
        raw = await (self._collect or self._browser_collect)(ctx, data_in)
        data.consent = raw.consent
        data.before = classify_phase(raw.before_requests, raw.before_cookies, db, site)
        data.status["consent"] = PartStatus.OK
        if raw.consent.reject in CLICKED and raw.after_requests is not None:
            data.after = classify_phase(raw.after_requests, raw.after_cookies or [], db, site)
            data.status["reject"] = PartStatus.OK
        elif raw.consent.reject == RejectState.FAILED:
            data.status["reject"] = PartStatus.UNAVAILABLE
        else:
            data.status["reject"] = PartStatus.NOT_APPLICABLE
        await self._supply(ctx, data, raw.scripts)
        if axe_source and raw.axe_runs:
            data.axe = group_violations(raw.axe_runs)
            data.axe_runs = len(raw.axe_runs)
            data.status["axe"] = PartStatus.OK
        else:
            data.status["axe"] = PartStatus.UNAVAILABLE
        ctx.meta["privacy"] = data.summary()
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

    async def _supply(
        self, ctx: DeepContext, data: PrivacyData, scripts: dict[str, str | None]
    ) -> None:
        s = self.settings
        merged = dict(scripts)
        if ctx.base is not None:
            # Faz 1 bundle'ları (aynı origin, lazy chunk'lar dahil) tarayıcının yüklemediği
            # parçaları da kapsar
            for script in ctx.base.scripts:
                if not script.inline and script.content:
                    merged.setdefault(script.url.split("?", 1)[0], script.content)
            for url in ctx.base.all_script_urls:
                merged.setdefault(url.split("?", 1)[0], None)
        data.script_urls = list(merged)
        data.scripts_scanned = sum(1 for b in merged.values() if b)
        data.risky = risky_scripts(merged, s.deep_risky_script_domains)
        data.status["scripts"] = PartStatus.OK
        retire = load_current_retire(s)
        if retire is None:
            data.status["libraries"] = PartStatus.UNAVAILABLE
            return
        # Düzenli ifadeler CPU yoğun: olay döngüsünü (olay akışı, diğer şeritler) bloklamasın
        data.libraries = await asyncio.to_thread(scan_libraries, retire, merged)
        data.status["libraries"] = PartStatus.OK

    async def _browser_collect(self, ctx: DeepContext, data_in: PrivacyInput) -> PrivacyRaw:
        from launchcheck.deep.browser.launcher import BrowserLimits, BrowserSession
        from launchcheck.deep.browser.privacy import PrivacyCollector

        s = self.settings
        guard = self._guard or UrlGuard(timeout_s=s.dns_timeout_s)
        limits = BrowserLimits.from_settings(s)
        async with BrowserSession.open(guard, s.bot_user_agent, limits) as session:
            return await PrivacyCollector(s).collect(session, data_in)
