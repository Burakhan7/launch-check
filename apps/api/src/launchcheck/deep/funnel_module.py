"""Huni yürüyüşü modülü (tarayıcı şeridi, gizlilikten sonra; PHASE2 §6.6, §8.6).

Kayıt ve fiyat sayfası adayları render modülünün topladığı link grafiğinden gelir (tık mesafesi
BFS ile). Yürüyüş mobilde yapılır; adımlar ve ekran görüntüleri huni haritası olarak
`meta.funnel`'a yazılır. Parçalar: signup (kayıt formu incelemesi), path (adım/tık/süre),
pricing (fiyat görünürlüğü ve KDV ifadesi).
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.checks.web.conversion import auth_target
from launchcheck.config import Settings, get_settings
from launchcheck.db.models import ScanAsset
from launchcheck.deep.browser.funnel import FunnelInput, FunnelRaw
from launchcheck.deep.context import DeepContext
from launchcheck.deep.funnel.analysis import (
    distance,
    link_distances,
    paid_signals,
    pick_signup,
    pricing_info,
)
from launchcheck.deep.funnel_data import FunnelData, FunnelStep, PartStatus
from launchcheck.deep.modules import DeepModule, Lane, ModuleOutput
from launchcheck.security.ssrf import UrlGuard

CHECK_BUDGET_S = 6
MAX_CANDIDATES = 3
STEP_CHECKS = {
    "signup": ("web.funnel.signup_friction", "web.funnel.mobile_input_types"),
    "pricing": ("web.funnel.price_hidden", "web.funnel.vat_excluded_price"),
}


class FunnelBlockedError(Exception):
    """Site bot doğrulaması gösterdi (render modülü)."""


def candidates(ctx: DeepContext, home: str) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """(kayıt adayları, fiyat adayları): (adres, tık mesafesi), yakından uzağa."""
    from launchcheck.deep.browser.render import page_role

    render = ctx.render
    distances = link_distances(render, home)
    urls: list[str] = []
    failed: set[str] = set()
    if render is not None:
        for page in render.pages:
            if page.status is not None and page.status >= 400:
                failed.add(page.url)
            urls.extend([page.url, *page.internal_links])
    signup: dict[str, int] = {}
    pricing: dict[str, int] = {}
    for url in dict.fromkeys(urls):
        if url in failed:
            continue
        clicks = distance(distances, url)
        if clicks is None or clicks == 0:
            continue
        if auth_target(url) == "register":
            signup.setdefault(url, clicks)
        elif page_role(url) == "pricing":
            pricing.setdefault(url, clicks)

    def ordered(found: dict[str, int]) -> list[tuple[str, int]]:
        return sorted(found.items(), key=lambda item: item[1])[:MAX_CANDIDATES]

    return ordered(signup), ordered(pricing)


class FunnelModule(DeepModule):
    name = "funnel"
    lane = Lane.BROWSER

    def __init__(
        self,
        settings: Settings | None = None,
        guard: UrlGuard | None = None,
        collect: Callable[[DeepContext, FunnelInput], Awaitable[FunnelRaw]] | None = None,
    ) -> None:
        self._settings = settings
        self._guard = guard
        self._collect = collect

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    def budget_s(self, settings: Settings) -> float:
        return settings.deep_budget_funnel_s

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        s = self.settings
        if ctx.meta.get("deep_blocked"):
            raise FunnelBlockedError()
        home = (
            ctx.render.home.final_url or ctx.render.home.url
            if ctx.render is not None and ctx.render.home is not None
            else ctx.target_url.rstrip("/") + "/"
        )
        parts = urlsplit(home)
        data = FunnelData(site=f"{parts.scheme}://{parts.netloc}")
        ctx.funnel = data
        signup_urls, pricing_urls = candidates(ctx, home)

        def save(kind: str, url: str, body: bytes, width: int, height: int) -> ScanAsset | None:
            if ctx.assets is None:
                return None
            asset = ctx.assets.save(ctx.scan_id, kind, url, body, width, height)
            out.assets.append(asset)
            return asset

        data_in = FunnelInput(
            home_url=home,
            signup_candidates=signup_urls,
            pricing_candidates=pricing_urls,
            save_screenshot=save,
            deadline=time.monotonic() + self.budget_s(s) - CHECK_BUDGET_S,
        )
        raw = await (self._collect or self._browser_collect)(ctx, data_in)
        build(data, raw, ctx)
        await ctx.events.emit(self.name, "deep.event.funnel_walked", steps=len(data.steps))
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
        attach_issues(data, {f.check_id for f in result.findings})
        ctx.meta["funnel"] = data.summary()

    async def _browser_collect(self, ctx: DeepContext, data_in: FunnelInput) -> FunnelRaw:
        from launchcheck.deep.browser.funnel import FunnelCollector
        from launchcheck.deep.browser.launcher import BrowserLimits, BrowserSession

        s = self.settings
        guard = self._guard or UrlGuard(timeout_s=s.dns_timeout_s)
        limits = BrowserLimits.from_settings(s)
        async with BrowserSession.open(guard, s.bot_user_agent, limits) as session:
            return await FunnelCollector(s).collect(session, data_in)


def build(data: FunnelData, raw: FunnelRaw, ctx: DeepContext) -> None:
    """Ham adımlardan huni verisi: kayıt formu, tık sayısı, fiyat bilgisi ve mesafesi."""
    data.cta_text = raw.cta_text
    home_inspect: dict[str, Any] = {}
    for step in raw.steps:
        data.steps.append(
            FunnelStep(
                kind=step.kind,
                url=urlsplit(step.url).path or "/",
                clicks=step.clicks,
                load_ms=step.load_ms,
                via=step.via,
                asset_id=str(step.asset.id) if step.asset is not None else None,
            )
        )
        if step.kind == "home":
            home_inspect = step.inspect
        if step.kind == "signup" and data.signup is None:
            form = pick_signup(step.url, step.inspect)
            if form is not None:
                data.signup, data.signup_clicks = form, step.clicks
    if not raw.steps:
        for part in data.status:
            data.status[part] = PartStatus.UNAVAILABLE
        return
    found = PartStatus.OK if data.signup is not None else PartStatus.NO_DATA
    data.status["signup"] = data.status["path"] = found
    _pricing(data, raw, ctx, home_inspect)


def _pricing(data: FunnelData, raw: FunnelRaw, ctx: DeepContext, home: dict[str, Any]) -> None:
    pricing_step = next((s for s in raw.steps if s.kind == "pricing"), None)
    reach: list[int] = []
    if home.get("prices"):
        data.pricing.append(pricing_info(raw.steps[0].url, home))
        reach.append(0)
    if pricing_step is not None:
        info = pricing_info(pricing_step.url, pricing_step.inspect)
        data.pricing.append(info)
        data.pricing_page = info.page
        if info.prices:
            reach.append(pricing_step.clicks)
    if ctx.render is not None and ctx.render.home is not None:
        distances = link_distances(ctx.render, ctx.render.home.final_url or ctx.render.home.url)
        for page in ctx.render.pages:
            clicks = distance(distances, page.url)
            if page.sd.prices and clicks is not None:
                reach.append(clicks)
    data.price_distance = min(reach) if reach else None
    data.paid_signals = paid_signals(
        ctx.render, str(home.get("text") or ""), pricing_step is not None
    )
    has_data = bool(data.paid_signals) or any(p.prices for p in data.pricing)
    data.status["pricing"] = PartStatus.OK if has_data else PartStatus.NO_DATA


def attach_issues(data: FunnelData, found: set[str]) -> None:
    """Huni haritasında her adımın altında o adımdaki bulgular görünsün (rapor, §8.6)."""
    for step in data.steps:
        ids = list(STEP_CHECKS.get(step.kind, ()))
        if step.kind in ("signup", "cta", "login"):
            ids.append("web.funnel.too_many_steps")
        if step.kind == "home":
            ids.append("web.funnel.price_hidden")
        step.issues = [c for c in dict.fromkeys(ids) if c in found]
