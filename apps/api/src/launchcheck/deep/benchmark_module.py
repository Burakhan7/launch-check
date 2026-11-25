"""Kıyas modülü (tarayıcı şeridi, huniden sonra; PHASE2 §7).

1. Kategori: kullanıcı seçtiyse o, değilse kural tabanlı tespit (belirsizse referans örnek yok).
2. Örnekler (en fazla 6): önce kullanıcının rakipleri (canlı taranır, 24 saat ortak önbellek),
   sonra kategorinin referans siteleri (haftalık taranmış; Faz 2 sırasında ziyaret edilmez).
3. Kullanıcının sitesi örneklerle AYNI yöntemle taranır; anatomiler deterministik karşılaştırılır.
Rakip ve referans sitelerde hiçbir güvenlik check'i çalışmaz, sadece anatomi çıkarılır;
robots.txt izin vermiyorsa site atlanır ve raporda söylenir.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.checks.shared.text import fold
from launchcheck.config import Settings, get_settings
from launchcheck.db.models import ScanAsset
from launchcheck.deep.benchmark.anatomy import build_anatomy
from launchcheck.deep.benchmark.cache import AnatomyCache, ShotStore
from launchcheck.deep.benchmark.categories import SiteSignals, category_keys, detect_category
from launchcheck.deep.benchmark.compare import MIN_EXAMPLES, compare
from launchcheck.deep.benchmark.robots import RobotsPolicy
from launchcheck.deep.benchmark_data import BenchmarkData, Example, PartStatus
from launchcheck.deep.browser.benchmark import SiteCrawl
from launchcheck.deep.context import DeepContext
from launchcheck.deep.modules import DeepModule, Lane, ModuleOutput

logger = logging.getLogger(__name__)

MAX_EXAMPLES = 6
MAX_SHOWN = 3  # yan yana ilk ekranlarda en fazla 3 örnek


@dataclass(frozen=True)
class ReferenceExample:
    origin: str
    anatomy: dict[str, Any]
    popularity: dict[str, Any]
    shots: dict[str, dict[str, Any]]  # mobile/desktop → {path, width, height}


Crawl = Callable[[list[str], dict[str, RobotsPolicy]], Awaitable[dict[str, SiteCrawl]]]
Robots = Callable[[str], Awaitable[RobotsPolicy]]
FieldLcp = Callable[[str], Awaitable[float | None]]
References = Callable[[str], Awaitable[list[ReferenceExample]]]


class BenchmarkBlockedError(Exception):
    """Site bot doğrulaması gösterdi (render modülü)."""


def anatomy_from_crawl(crawl: SiteCrawl, field_lcp_ms: float | None) -> dict[str, Any]:
    links = [str(u) for u in crawl.home.get("links") or []]
    pricing_clicks = (
        1
        if crawl.pricing_url and crawl.pricing_url in links
        else (2 if crawl.pricing_url else None)
    )
    return build_anatomy(
        crawl.home,
        home_inspect=crawl.home_inspect,
        pricing_inspect=crawl.pricing_inspect,
        pricing_clicks=pricing_clicks,
        signup_url=crawl.signup_url,
        signup_inspect=crawl.signup_inspect,
        field_lcp_ms=field_lcp_ms,
    )


def site_signals(ctx: DeepContext) -> SiteSignals:
    from launchcheck.deep.browser.render import page_role
    from launchcheck.deep.funnel.analysis import has_phrase

    texts: list[str] = []
    hosts: list[str] = []
    prices = 0
    cart = False
    pricing = False
    if ctx.render is not None and ctx.render.home is not None:
        home = ctx.render.home
        first = home.mobile.first_screen if home.mobile else []
        texts = [home.title, home.h1, *home.sd.headings, *(i.text for i in first)]
        hosts = [(urlsplit(u).hostname or "") for u in home.external_links]
        prices = sum(p.sd.prices for p in ctx.render.pages)
        cart = any(
            has_phrase("structured_data.add_to_cart", fold(" ".join(p.sd.actions)))
            for p in ctx.render.pages
        )
        pricing = any(page_role(u) == "pricing" for u in home.internal_links)
    return SiteSignals(ctx.parent_meta, texts, hosts, prices, cart, pricing)


class BenchmarkModule(DeepModule):
    name = "benchmark"
    lane = Lane.BROWSER

    def __init__(
        self,
        settings: Settings | None = None,
        crawl: Crawl | None = None,
        robots: Robots | None = None,
        field_lcp: FieldLcp | None = None,
        references: References | None = None,
        cache: AnatomyCache | None = None,
        shots: ShotStore | None = None,
    ) -> None:
        self._settings = settings
        self._crawl = crawl
        self._robots = robots
        self._field_lcp = field_lcp
        self._references = references
        self._cache = cache
        self._shots = shots

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    def budget_s(self, settings: Settings) -> float:
        return settings.deep_budget_benchmark_s

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        if ctx.meta.get("deep_blocked"):
            raise BenchmarkBlockedError()
        s = self.settings
        user_origin = _origin(ctx)
        data = BenchmarkData(site=user_origin)
        ctx.benchmark = data
        self._category(ctx, data)
        competitors = [str(c).rstrip("/") for c in ctx.options.get("competitors") or []]
        competitors = competitors[: s.deep_max_competitors]
        references: list[ReferenceExample] = []
        if ctx.options.get("use_reference", True) and data.category:
            loader = self._references or default_references
            try:
                references = await loader(data.category)
            except Exception as exc:
                logger.warning("referans siteler okunamadı (%s)", type(exc).__name__)
        await ctx.events.emit(
            self.name,
            "deep.event.benchmark_started",
            competitors=len(competitors),
            references=min(len(references), MAX_EXAMPLES - len(competitors)),
        )
        crawls, cached = await self._competitors(competitors, user_origin)
        user_crawl = crawls.pop(user_origin, None)
        field = await self._field_values(
            [user_origin, *[c for c in competitors if c not in cached]]
        )
        if user_crawl is not None and user_crawl.ok:
            data.user = anatomy_from_crawl(user_crawl, field.get(user_origin))
            data.user_assets = self._save_crawl_shots(ctx, out, user_crawl, "benchmark_self")
        for origin in competitors:
            data.examples.append(await self._competitor_example(origin, crawls, cached, field))
        for ref in references:
            if len(data.examples) >= MAX_EXAMPLES:
                break
            if ref.origin.rstrip("/") in competitors or ref.origin.rstrip("/") == user_origin:
                continue
            data.examples.append(
                Example(
                    ref.origin,
                    "reference",
                    True,
                    popularity=ref.popularity,
                    anatomy=ref.anatomy,
                    shots=ref.shots,
                )
            )
        self._attach_example_shots(ctx, out, data)
        self._compare(ctx, data)
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
        ctx.meta["benchmark"] = data.summary()

    # --- kategori ve örnekler

    def _category(self, ctx: DeepContext, data: BenchmarkData) -> None:
        chosen = ctx.options.get("category")
        detected, scores = detect_category(site_signals(ctx))
        data.category_scores = scores
        if isinstance(chosen, str) and chosen in category_keys():
            data.category, data.category_source = chosen, "user"
        elif detected is not None:
            data.category, data.category_source = detected, "detected"

    async def _competitors(
        self, competitors: list[str], user_origin: str
    ) -> tuple[dict[str, SiteCrawl], dict[str, dict[str, Any]]]:
        """Önbellekte olmayan rakipler + kullanıcının sitesi taranır (aynı oturum)."""
        cached: dict[str, dict[str, Any]] = {}
        for origin in competitors:
            hit = await self._anatomy_cache().get(origin)
            if hit is not None and hit.get("anatomy"):
                cached[origin] = hit
        todo = [c for c in competitors if c not in cached]
        policies: dict[str, RobotsPolicy] = {}
        robots = self._robots or self._default_robots
        for origin in todo:
            policies[origin] = await robots(origin)
        crawl = self._crawl or self._browser_crawl
        # Kullanıcının kendi sitesi için robots.txt politikası yok (taramayı kendisi başlattı)
        return await crawl([user_origin, *todo], policies), cached

    async def _competitor_example(
        self,
        origin: str,
        crawls: dict[str, SiteCrawl],
        cached: dict[str, dict[str, Any]],
        field: dict[str, float | None],
    ) -> Example:
        if origin in cached:
            hit = cached[origin]
            anatomy = hit.get("anatomy") or {}
            return Example(
                origin,
                "competitor",
                True,
                cached=True,
                anatomy=anatomy,
                popularity={"crux": (anatomy.get("perf") or {}).get("field_data")},
                shots=hit.get("shots") or {},
            )
        crawl = crawls.get(origin)
        if crawl is None or not crawl.ok:
            return Example(
                origin, "competitor", False, reason=crawl.reason if crawl else "unreachable"
            )
        anatomy = anatomy_from_crawl(crawl, field.get(origin))
        shots: dict[str, Any] = {}
        store = self._shot_store()
        if store is not None:
            for kind, (webp, width, height) in crawl.shots.items():
                saved = await asyncio.to_thread(store.save, origin, kind, webp, width, height)
                if saved is not None:
                    shots[kind] = {"path": saved.path, "width": saved.width, "height": saved.height}
        await self._anatomy_cache().put(origin, {"anatomy": anatomy, "shots": shots})
        return Example(
            origin,
            "competitor",
            True,
            anatomy=anatomy,
            popularity={"crux": anatomy["perf"]["field_data"]},
            shots=shots,
        )

    async def _field_values(self, origins: list[str]) -> dict[str, float | None]:
        fn = self._field_lcp or self._default_field_lcp
        values = await asyncio.gather(*(fn(o) for o in origins), return_exceptions=True)
        return {
            o: (v if isinstance(v, int | float) else None)
            for o, v in zip(origins, values, strict=True)
        }

    def _compare(self, ctx: DeepContext, data: BenchmarkData) -> None:
        valid = data.valid
        if data.user is None or len(valid) < MIN_EXAMPLES:
            for part in data.status:
                data.status[part] = PartStatus.NOT_ENOUGH
            return
        data.comparison = compare(data.user, [e.anatomy for e in valid if e.anatomy])
        data.status["compare"] = PartStatus.OK
        data.status["speed"] = PartStatus.OK if data.comparison.speed else PartStatus.NO_DATA

    # --- ekran görüntüleri (taramanın kendi asset'i olarak; 7 gün)

    def _save_asset(
        self, ctx: DeepContext, out: ModuleOutput, kind: str, url: str, shot: tuple[bytes, int, int]
    ) -> str | None:
        if ctx.assets is None:
            return None
        try:
            asset: ScanAsset = ctx.assets.save(ctx.scan_id, kind, url, shot[0], shot[1], shot[2])
        except ValueError:
            return None
        out.assets.append(asset)
        return str(asset.id)

    def _save_crawl_shots(
        self, ctx: DeepContext, out: ModuleOutput, crawl: SiteCrawl, prefix: str
    ) -> dict[str, str]:
        saved = {}
        for kind, shot in crawl.shots.items():
            asset_id = self._save_asset(ctx, out, f"{prefix}_{kind}", crawl.origin + "/", shot)
            if asset_id:
                saved[kind] = asset_id
        return saved

    def _attach_example_shots(
        self,
        ctx: DeepContext,
        out: ModuleOutput,
        data: BenchmarkData,
    ) -> None:
        store = self._shot_store()
        if store is None:
            return
        shown = 0
        for example in data.valid:
            if shown >= MAX_SHOWN:
                break
            for kind in ("mobile", "desktop"):
                raw = store.read(example.shots.get(kind))
                if raw is None:
                    continue
                asset_id = self._save_asset(
                    ctx, out, f"benchmark_example_{kind}", example.origin + "/", raw
                )
                if asset_id:
                    example.assets[kind] = asset_id
            shown += 1

    # --- varsayılan bağımlılıklar

    def _anatomy_cache(self) -> AnatomyCache:
        if self._cache is None:
            redis_client: Any = None
            try:
                from redis.asyncio import Redis

                redis_client = Redis.from_url(self.settings.redis_url)
            except Exception:  # pragma: no cover - Redis yoksa önbelleksiz
                redis_client = None
            self._cache = AnatomyCache(redis_client, self.settings.benchmark_cache_ttl_h * 3600)
        return self._cache

    def _shot_store(self) -> ShotStore | None:
        if self._shots is None:
            from pathlib import Path

            s = self.settings
            self._shots = ShotStore(Path(s.assets_dir), s.deep_max_asset_bytes)
        return self._shots

    async def _default_robots(self, origin: str) -> RobotsPolicy:
        from launchcheck.collectors.fetcher import SafeFetcher
        from launchcheck.deep.benchmark.robots import fetch_policy
        from launchcheck.security.ssrf import UrlGuard

        s = self.settings
        fetcher = SafeFetcher(s, UrlGuard(timeout_s=s.dns_timeout_s))
        try:
            return await fetch_policy(fetcher, origin)
        finally:
            await fetcher.aclose()

    async def _default_field_lcp(self, origin: str) -> float | None:
        from launchcheck.deep.perf.crux import CruxClient

        s = self.settings
        client = CruxClient(s.google_api_key, s.deep_crux_endpoint, s.deep_crux_timeout_s)
        _, data = await client.field_data(origin + "/", origin)
        return data.lcp_ms if data is not None else None

    async def _browser_crawl(
        self, origins: list[str], policies: dict[str, RobotsPolicy]
    ) -> dict[str, SiteCrawl]:
        from launchcheck.deep.browser.benchmark import SiteCrawler
        from launchcheck.deep.browser.launcher import BrowserLimits, BrowserSession
        from launchcheck.security.ssrf import UrlGuard

        s = self.settings
        guard = UrlGuard(timeout_s=s.dns_timeout_s)
        crawler = SiteCrawler(s.deep_competitor_max_pages)
        sem = asyncio.Semaphore(s.deep_benchmark_concurrency)
        out: dict[str, SiteCrawl] = {}
        async with BrowserSession.open(
            guard, s.bot_user_agent, BrowserLimits.from_settings(s)
        ) as session:

            async def one(origin: str) -> None:
                policy = policies.get(origin)
                allowed = policy.allowed if policy is not None else (lambda url: True)
                async with sem:
                    out[origin] = await crawler.crawl(session, origin, allowed)

            await asyncio.gather(*(one(o) for o in origins))
        return out


def _origin(ctx: DeepContext) -> str:
    url = (
        ctx.render.home.final_url or ctx.render.home.url
        if ctx.render is not None and ctx.render.home is not None
        else ctx.target_url
    )
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


async def default_references(category: str) -> list[ReferenceExample]:
    """DB'deki referans siteler (aktif veya saha verisi olmayan, anatomisi olan)."""
    from sqlalchemy import select

    from launchcheck.db.models import ReferenceSite, ReferenceSiteStatus
    from launchcheck.db.session import get_sessionmaker

    usable = (ReferenceSiteStatus.ACTIVE, ReferenceSiteStatus.NO_FIELD_DATA)
    async with get_sessionmaker()() as session:
        rows = (
            await session.scalars(
                select(ReferenceSite).where(
                    ReferenceSite.category == category, ReferenceSite.status.in_(usable)
                )
            )
        ).all()
    out = []
    for row in rows:
        anatomy = dict(row.anatomy or {})
        shots = anatomy.pop("_shots", {}) or {}
        if anatomy.get("sections") is not None:
            out.append(ReferenceExample(row.origin, anatomy, dict(row.popularity or {}), shots))
    return out
