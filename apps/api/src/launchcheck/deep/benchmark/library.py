"""Referans kütüphanesi (PHASE2 §7.2): YAML → DB senkronu ve kademeli haftalık yenileme.

- `sync_reference_sites`: worker-deep başlarken YAML'daki (kategori, site) çiftleri DB'ye yazılır,
  listeden çıkarılanlar silinir.
- `refresh_due_sites`: saatlik cron; en eski taranmış `deep_reference_sites_per_run` site (sırayla,
  aralarında en az `deep_reference_gap_s`). Her site için robots.txt → tarama (en fazla 6 sayfa,
  sadece anatomi) → CrUX origin. Tüm liste böylece haftada bir yenilenir ve tek seferde
  worker'ı uzun süre meşgul etmez (cron işleri derin taramalarla aynı sırayı paylaşır).
- `refresh_popularity`: haftalık; Majestic Million (CC BY 3.0) sıralaması akış hâlinde okunur.
Durumlar: active | no_field_data (CrUX'ta yok; listede kalır, raporda not) | robots_blocked |
failed. Referans sitelerde hiçbir güvenlik check'i çalışmaz.
"""

import asyncio
import csv
import io
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.config import Settings
from launchcheck.db.models import ReferenceSite, ReferenceSiteStatus, utcnow
from launchcheck.deep.benchmark.cache import ShotStore
from launchcheck.deep.benchmark.categories import load_categories
from launchcheck.deep.benchmark.robots import RobotsPolicy
from launchcheck.deep.browser.benchmark import SiteCrawl

logger = logging.getLogger(__name__)

CrawlSite = Callable[[str, RobotsPolicy], Awaitable[SiteCrawl]]
Robots = Callable[[str], Awaitable[RobotsPolicy]]
FieldLcp = Callable[[str], Awaitable[float | None]]
Lines = Callable[[], AsyncIterator[str]]


def origin_of(site: str) -> str:
    site = site.strip().lower().rstrip("/")
    return site if site.startswith(("http://", "https://")) else f"https://{site}"


async def sync_reference_sites(sm: async_sessionmaker[AsyncSession]) -> int:
    wanted = {(c.key, origin_of(s)) for c in load_categories() for s in c.sites}
    async with sm() as session:
        rows = (await session.scalars(select(ReferenceSite))).all()
        existing = {(r.category, r.origin): r for r in rows}
        for key, row in existing.items():
            if key not in wanted:
                await session.delete(row)
        added = 0
        for category, origin in sorted(wanted - set(existing)):
            session.add(ReferenceSite(category=category, origin=origin))
            added += 1
        await session.commit()
    return added


async def refresh_due_sites(
    sm: async_sessionmaker[AsyncSession],
    settings: Settings,
    crawl_site: CrawlSite,
    robots: Robots,
    field_lcp: FieldLcp,
    shots: ShotStore | None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[str]:
    from launchcheck.deep.benchmark_module import anatomy_from_crawl

    cutoff = utcnow() - timedelta(days=settings.deep_reference_refresh_days)
    async with sm() as session:
        due = (
            await session.scalars(
                select(ReferenceSite)
                .where(
                    or_(
                        ReferenceSite.last_scanned_at.is_(None),
                        ReferenceSite.last_scanned_at < cutoff,
                    )
                )
                .order_by(ReferenceSite.last_scanned_at.asc().nulls_first(), ReferenceSite.id)
                .limit(settings.deep_reference_sites_per_run)
            )
        ).all()
        origins = [(r.id, r.origin) for r in due]
    done: list[str] = []
    for index, (row_id, origin) in enumerate(origins):
        if index:
            await sleep(settings.deep_reference_gap_s)  # siteler arası nezaket aralığı
        update: dict[str, Any] = {"last_scanned_at": utcnow(), "last_error": None}
        policy = await robots(origin)
        if not policy.reachable or not policy.allowed(origin + "/"):
            update["status"] = (
                ReferenceSiteStatus.ROBOTS_BLOCKED
                if policy.reachable
                else ReferenceSiteStatus.FAILED
            )
            update["last_error"] = "robots_blocked" if policy.reachable else "robots_unreachable"
        else:
            crawl = await crawl_site(origin, policy)
            if not crawl.ok:
                update["status"] = ReferenceSiteStatus.FAILED
                update["last_error"] = crawl.reason or "unreachable"
            else:
                field = await field_lcp(origin)
                anatomy = anatomy_from_crawl(crawl, field)
                anatomy["_shots"] = _save_shots(shots, origin, crawl)
                update["anatomy"] = anatomy
                has_key = bool(settings.google_api_key)
                update["status"] = (
                    ReferenceSiteStatus.NO_FIELD_DATA
                    if has_key and field is None
                    else ReferenceSiteStatus.ACTIVE
                )
                update["popularity_crux"] = None if not has_key else field is not None
        await _apply(sm, row_id, update)
        done.append(origin)
    return done


def _save_shots(shots: ShotStore | None, origin: str, crawl: SiteCrawl) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if shots is None:
        return out
    for kind, (webp, width, height) in crawl.shots.items():
        saved = shots.save(origin, kind, webp, width, height, reference=True)
        if saved is not None:
            out[kind] = {"path": saved.path, "width": saved.width, "height": saved.height}
    return out


async def _apply(sm: async_sessionmaker[AsyncSession], row_id: int, update: dict[str, Any]) -> None:
    async with sm() as session:
        row = await session.get(ReferenceSite, row_id)
        if row is None:
            return
        crux = update.pop("popularity_crux", "unset")
        for key, value in update.items():
            setattr(row, key, value)
        if crux != "unset":
            row.popularity = {**(row.popularity or {}), "crux": crux}
        await session.commit()


def domain_of(origin: str) -> str:
    host = (urlsplit(origin).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


async def majestic_ranks(lines: AsyncIterator[str], domains: set[str]) -> dict[str, int]:
    """Majestic Million CSV'si (GlobalRank,TldRank,Domain,...) akış hâlinde; hepsi bulununca
    durur. Dosyanın tamamı belleğe alınmaz."""
    found: dict[str, int] = {}
    header: list[str] | None = None
    async for line in lines:
        row = next(csv.reader(io.StringIO(line)), [])
        if header is None:
            header = [h.strip().lower() for h in row]
            continue
        try:
            domain = row[header.index("domain")].strip().lower()
            rank = int(row[header.index("globalrank")])
        except (ValueError, IndexError):
            continue
        if domain in domains and domain not in found:
            found[domain] = rank
            if len(found) == len(domains):
                break
    return found


async def refresh_popularity(sm: async_sessionmaker[AsyncSession], lines: Lines) -> int:
    async with sm() as session:
        rows = (await session.scalars(select(ReferenceSite))).all()
        by_domain: dict[str, list[ReferenceSite]] = {}
        for row in rows:
            by_domain.setdefault(domain_of(row.origin), []).append(row)
        ranks = await majestic_ranks(lines(), set(by_domain))
        for domain, items in by_domain.items():
            for row in items:
                row.popularity = {**(row.popularity or {}), "majestic_rank": ranks.get(domain)}
        await session.commit()
    return len(ranks)


async def stream_lines(settings: Settings) -> AsyncIterator[str]:
    """Majestic listesini SSRF korumalı istemciyle satır satır okur (boyut sınırlı)."""
    import httpx

    from launchcheck.security.ssrf import GuardedTransport, UrlGuard

    if not settings.deep_majestic_url:
        return
    guard = UrlGuard(timeout_s=settings.dns_timeout_s)
    await guard.check_url(settings.deep_majestic_url)
    read = 0
    async with (
        httpx.AsyncClient(transport=GuardedTransport(guard), timeout=60, trust_env=False) as client,
        client.stream(
            "GET", settings.deep_majestic_url, headers={"User-Agent": settings.bot_user_agent}
        ) as response,
    ):
        if response.status_code != 200:
            logger.info("Majestic listesi alınamadı (%s)", response.status_code)
            return
        async for line in response.aiter_lines():
            read += len(line) + 1
            if read > settings.deep_majestic_max_bytes:
                return
            yield line
