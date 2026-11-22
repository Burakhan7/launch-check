"""Hız modülü (Lighthouse şeridi, PHASE2 §6.2).

Sıra: ana sayfa × N mobil → ana sayfa × 1 masaüstü → kilit sayfa × N mobil (render modülü
kilit sayfayı bulana kadar bekler; bulamazsa atlanır). Ortanca alınır. CrUX saha verisi aynı
anda sorgulanır. Bütçe yetmeyecekse yeni ölçüm başlatılmaz; elde olanlarla check'ler çalışır.
Ana sayfanın ortanca mobil ölçümünden filmstrip şeridi (WebP) asset olarak kaydedilir.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.config import Settings, get_settings
from launchcheck.deep.context import DeepContext
from launchcheck.deep.modules import DeepModule, Lane, ModuleOutput
from launchcheck.deep.perf.crux import CruxClient
from launchcheck.deep.perf.filmstrip import strip_webp
from launchcheck.deep.perf.lighthouse import LighthouseError, parse_report
from launchcheck.deep.perf_data import DESKTOP, MOBILE, FieldData, FieldStatus, PerfData
from launchcheck.security.ssrf import UrlGuard

logger = logging.getLogger(__name__)

# (url, form_factor, kalan süre) → Lighthouse rapor JSON'u
Measure = Callable[[str, str, float], Awaitable[dict[str, Any]]]
# (sayfa url, origin) → (durum, saha verisi)
FieldQuery = Callable[[str, str], Awaitable[tuple[str, FieldData | None]]]

CHECK_BUDGET_S = 10  # check'ler + asset + kapanış için ayrılan süre
FIRST_RUN_ESTIMATE_S = 35  # ilk ölçümden önce tahmini süre
MAX_FAILURES_PER_URL = 2
FIELD_CHECKS = ("web.perf.inp_slow",)
LAB_CHECKS = (
    "web.perf.blank_screen_long",
    "web.perf.unused_js",
    "web.perf.oversized_images",
    "web.perf.no_compression_or_cache",
    "web.perf.heavy_third_party",
)


class PerfUnavailableError(Exception):
    """Ne Lighthouse ölçümü ne saha verisi alınabildi."""


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class PerfModule(DeepModule):
    name = "perf"
    lane = Lane.LIGHTHOUSE

    def __init__(
        self,
        settings: Settings | None = None,
        guard: UrlGuard | None = None,
        measure: Measure | None = None,
        field_query: FieldQuery | None = None,
        allow: frozenset[tuple[str, int]] = frozenset(),  # sadece testler (yerel sunucu)
    ) -> None:
        self._settings = settings
        self._guard = guard
        self._measure = measure
        self._field_query = field_query
        self._allow = allow

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    def budget_s(self, settings: Settings) -> float:
        return settings.deep_budget_lighthouse_s

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        s = self.settings
        home = ctx.target_url.rstrip("/") + "/"
        data = PerfData(home_url=home)
        deadline = time.monotonic() + self.budget_s(s) - CHECK_BUDGET_S
        field_task = asyncio.create_task(self._field(home, data))
        try:
            async with self._measurer() as measure:
                await self._measure_all(ctx, data, measure, deadline)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(field_task), max(1.0, s.deep_crux_timeout_s))
        finally:
            if not field_task.done():
                field_task.cancel()
        await ctx.events.emit(
            self.name,
            "deep.event.field_data_ok"
            if data.field_status == FieldStatus.OK
            else "deep.event.field_data_missing",
        )
        if not data.runs and data.field is None:
            raise PerfUnavailableError()
        ctx.perf = data
        self._save_filmstrip(ctx, data, out)
        ctx.meta["perf"] = summary(data)
        skipped = [*([] if data.runs else LAB_CHECKS), *([] if data.field else FIELD_CHECKS)]
        if skipped:
            ctx.note_checks("checks_skipped", list(skipped))
        checks = [
            c
            for c in load_builtin_checks().for_target(TargetType.WEB, 2)
            if c.phase == 2 and c.module == self.name and c.id not in skipped
        ]
        result = await run_checks(ctx, checks)
        out.findings.extend(result.findings)
        if result.failed:
            ctx.note_checks("checks_failed", result.failed)

    # --- ölçümler

    def _plan(self, home: str) -> list[tuple[str, str]]:
        s = self.settings
        return [(home, MOBILE)] * s.deep_lighthouse_runs + [
            (home, DESKTOP)
        ] * s.deep_lighthouse_desktop_runs

    async def _measure_all(
        self, ctx: DeepContext, data: PerfData, measure: Measure, deadline: float
    ) -> None:
        s = self.settings
        plan = self._plan(data.home_url)
        total = len(plan)  # kilit sayfa bulununca artar
        durations: list[float] = []
        failures: dict[str, int] = {}
        done = 0

        async def one(url: str, form_factor: str) -> None:
            nonlocal done
            if failures.get(url, 0) >= MAX_FAILURES_PER_URL:
                return
            estimate = max(durations) if durations else FIRST_RUN_ESTIMATE_S
            remaining = deadline - time.monotonic()
            if remaining < estimate:
                return
            started = time.monotonic()
            try:
                report = await measure(url, form_factor, remaining)
                run = parse_report(report, url, form_factor, s.deep_perf_blank_content_ratio)
            except LighthouseError as exc:
                failures[url] = failures.get(url, 0) + 1
                errors = ctx.meta.setdefault("perf_errors", [])
                errors.append(exc.code)
                logger.info("Lighthouse ölçümü kullanılamadı (%s)", exc.code)
            else:
                data.runs.append(run)
            durations.append(time.monotonic() - started)
            done += 1
            await ctx.events.emit(self.name, "deep.event.lighthouse_run", done=done, total=total)

        for url, form_factor in plan:
            await one(url, form_factor)
        key = await self._key_url(ctx, data.home_url, deadline)
        if key:
            data.key_url = key
            total += s.deep_lighthouse_runs
            for _ in range(s.deep_lighthouse_runs):
                await one(key, MOBILE)

    async def _key_url(self, ctx: DeepContext, home: str, deadline: float) -> str | None:
        wait = min(self.settings.deep_key_page_wait_s, deadline - time.monotonic())
        if wait > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(ctx.key_urls_ready.wait(), wait)
        home_key = home.rstrip("/")
        for url in ctx.key_urls:
            if url.rstrip("/") != home_key:
                return url
        return None

    @contextlib.asynccontextmanager
    async def _measurer(self) -> AsyncIterator[Measure]:
        if self._measure is not None:
            yield self._measure
            return
        from launchcheck.deep.browser.egress_proxy import EgressProxy
        from launchcheck.deep.perf.lighthouse import LighthouseConfig, LighthouseRunner

        s = self.settings
        guard = self._guard or UrlGuard(timeout_s=s.dns_timeout_s)
        proxy = EgressProxy(guard, s.deep_proxy_max_total_bytes, allow=self._allow)
        await proxy.start()
        try:
            runner = LighthouseRunner(LighthouseConfig.from_settings(s), proxy.port)

            async def measure(url: str, form_factor: str, remaining: float) -> dict[str, Any]:
                return await runner.run(url, form_factor, remaining)

            yield measure
        finally:
            await proxy.close()

    async def _field(self, home: str, data: PerfData) -> None:
        s = self.settings
        query = (
            self._field_query
            or CruxClient(s.google_api_key, s.deep_crux_endpoint, s.deep_crux_timeout_s).field_data
        )
        try:
            data.field_status, data.field = await query(home, origin_of(home))
        except Exception as exc:
            logger.info("saha verisi alınamadı (%s)", type(exc).__name__)
            data.field_status = FieldStatus.ERROR

    # --- çıktılar

    def _save_filmstrip(self, ctx: DeepContext, data: PerfData, out: ModuleOutput) -> None:
        run = data.median_run(data.home_url, MOBILE)
        if run is None or not run.frames or ctx.assets is None:
            return
        try:
            webp, width, height, frames_ms = strip_webp(
                run.frames, self.settings.deep_perf_filmstrip_max_ms
            )
            asset = ctx.assets.save(ctx.scan_id, "filmstrip", run.url, webp, width, height)
        except Exception as exc:
            logger.info("filmstrip kaydedilemedi (%s)", type(exc).__name__)
            return
        out.assets.append(asset)
        ctx.meta["perf_filmstrip"] = {"asset_id": str(asset.id), "frames_ms": frames_ms}


def summary(data: PerfData) -> dict[str, Any]:
    """Rapor için özet (`scan.meta.perf`): ortanca laboratuvar değerleri ve saha verisi."""

    def lab(url: str, form_factor: str) -> dict[str, Any] | None:
        runs = data.runs_for(url, form_factor)
        if not runs:
            return None
        median = data.median_run(url, form_factor)
        return {
            "url": url,
            "runs": len(runs),
            "lcp_ms": _round(data.median(url, "lcp_ms", form_factor)),
            "cls": _round(data.median(url, "cls", form_factor), 3),
            "fcp_ms": _round(data.median(url, "fcp_ms", form_factor)),
            "tbt_ms": _round(data.median(url, "tbt_ms", form_factor)),
            "speed_index_ms": _round(data.median(url, "speed_index_ms", form_factor)),
            "blank_ms": _round(data.median(url, "blank_ms", form_factor)),
            "scores": median.scores if median else {},
        }

    field = data.field
    return {
        "field_status": data.field_status,
        "field": None
        if field is None
        else {
            "scope": field.scope,
            "form_factor": field.form_factor,
            "lcp_ms": _round(field.lcp_ms),
            "cls": _round(field.cls, 3),
            "inp_ms": _round(field.inp_ms),
        },
        "home_mobile": lab(data.home_url, MOBILE),
        "home_desktop": lab(data.home_url, DESKTOP),
        "key_mobile": lab(data.key_url, MOBILE) if data.key_url else None,
    }


def _round(value: float | None, digits: int = 0) -> float | int | None:
    if value is None:
        return None
    return round(value, digits) if digits else round(value)
