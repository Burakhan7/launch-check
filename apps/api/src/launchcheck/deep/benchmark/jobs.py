"""worker-deep cron işleri için kıyas yardımcıları (gerçek tarayıcı, SSRF korumalı istemciler)."""

from pathlib import Path

from launchcheck.collectors.fetcher import SafeFetcher
from launchcheck.config import Settings
from launchcheck.deep.benchmark.cache import ShotStore
from launchcheck.deep.benchmark.robots import RobotsPolicy, fetch_policy
from launchcheck.deep.browser.benchmark import SiteCrawl
from launchcheck.security.ssrf import UrlGuard


def shot_store(settings: Settings) -> ShotStore:
    return ShotStore(Path(settings.assets_dir), settings.deep_max_asset_bytes)


async def robots_policy(settings: Settings, origin: str) -> RobotsPolicy:
    fetcher = SafeFetcher(settings, UrlGuard(timeout_s=settings.dns_timeout_s))
    try:
        return await fetch_policy(fetcher, origin)
    finally:
        await fetcher.aclose()


async def crawl_site(settings: Settings, origin: str, policy: RobotsPolicy) -> SiteCrawl:
    from launchcheck.deep.browser.benchmark import SiteCrawler
    from launchcheck.deep.browser.launcher import BrowserLimits, BrowserSession

    guard = UrlGuard(timeout_s=settings.dns_timeout_s)
    limits = BrowserLimits.from_settings(settings)
    async with BrowserSession.open(guard, settings.bot_user_agent, limits) as session:
        return await SiteCrawler(settings.deep_competitor_max_pages).crawl(
            session, origin, policy.allowed
        )


async def field_lcp(settings: Settings, origin: str) -> float | None:
    from launchcheck.deep.perf.crux import CruxClient

    client = CruxClient(
        settings.google_api_key, settings.deep_crux_endpoint, settings.deep_crux_timeout_s
    )
    _, data = await client.field_data(origin + "/", origin)
    return data.lcp_ms if data is not None else None
