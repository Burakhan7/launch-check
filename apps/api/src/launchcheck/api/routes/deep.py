"""Faz 2 (derin tarama) uçları: başlatma, canlı olaylar, asset'ler."""

import logging
import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from launchcheck.api.deep_queue import queue_info, waiting_count
from launchcheck.api.deps import enforce_rate_limit, get_url_guard
from launchcheck.api.errors import ApiError
from launchcheck.api.queue import DEEP_QUEUE, RUN_DEEP_SCAN_JOB, JobQueue, get_queue
from launchcheck.api.schemas import DeepScanCreated, DeepScanRequest, EventOut, EventsResponse
from launchcheck.checks.base import TargetType
from launchcheck.checks.shared.domains import registered_domain
from launchcheck.config import get_settings
from launchcheck.db.models import Scan, ScanAsset, ScanEvent, ScanStatus, utcnow
from launchcheck.db.session import get_session
from launchcheck.deep.benchmark.categories import category_keys
from launchcheck.deep.setup import asset_store
from launchcheck.i18n import event_text, normalize_lang
from launchcheck.security.ssrf import UrlGuard
from launchcheck.security.url import TargetValidationError, normalize_target_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scans", tags=["deep"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
GuardDep = Annotated[UrlGuard, Depends(get_url_guard)]
DAY_S = 86400
ASSET_HEADERS = {"Cache-Control": "private, max-age=3600", "X-Robots-Tag": "noindex"}


def _uuid(value: str, code: str = "not_found") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(code, 404) from exc


def _aware_age(scan: Scan) -> timedelta:
    created = scan.created_at
    if created.tzinfo is None:  # SQLite saat dilimini saklamaz
        created = created.replace(tzinfo=utcnow().tzinfo)
    return utcnow() - created


async def _parent(session: AsyncSession, scan_id: str) -> Scan:
    parent = await session.get(Scan, _uuid(scan_id, "parent_not_found"))
    if parent is None:
        raise ApiError("parent_not_found", 404)
    if (
        parent.target_type != TargetType.WEB
        or parent.phase != 1
        or parent.status != ScanStatus.DONE
    ):
        raise ApiError("parent_not_ready", 409)
    if _aware_age(parent) > timedelta(hours=get_settings().deep_parent_max_age_h):
        raise ApiError("parent_too_old", 409)
    return parent


async def _competitors(urls: list[str], parent: Scan, guard: UrlGuard) -> list[str]:
    """Rakip adresleri normalize origin'lere çevrilir; SSRF ön kontrolünden geçer. Kullanıcının
    kendi sitesi veya alt alan adı rakip olamaz."""
    own = registered_domain(normalize_target_url(parent.target_label).host)
    out: list[str] = []
    for raw in urls:
        try:
            target = normalize_target_url(raw)
            await guard.resolve_public(target.host, target.port)
        except TargetValidationError as exc:
            raise ApiError("invalid_competitor_url", 400) from exc
        if registered_domain(target.host) == own:
            raise ApiError("competitor_same_origin", 400)
        if target.origin not in out:
            out.append(target.origin)
    return out[: get_settings().deep_max_competitors]


async def _compare_to(
    session: AsyncSession, value: uuid.UUID | None, parent: Scan
) -> uuid.UUID | None:
    if value is None:
        return None
    previous = await session.get(Scan, value)
    if (
        previous is None
        or previous.phase != 2
        or previous.status != ScanStatus.DONE
        or previous.target_label != parent.target_label
    ):
        raise ApiError("invalid_compare_to", 400)
    return previous.id


@router.post("/{scan_id}/deep", status_code=202, response_model=DeepScanCreated)
async def create_deep_scan(
    scan_id: str,
    body: DeepScanRequest,
    request: Request,
    session: SessionDep,
    queue: QueueDep,
    guard: GuardDep,
) -> DeepScanCreated:
    settings = get_settings()
    parent = await _parent(session, scan_id)
    competitors = await _competitors(body.competitors, parent, guard)
    if body.category is not None and body.category not in category_keys():
        raise ApiError("invalid_category", 400)
    compare_to = await _compare_to(session, body.compare_to, parent)
    if await waiting_count(session) >= settings.deep_queue_max:
        raise ApiError("queue_full", 503)
    # Günlük kota sadece geçerli istekler için harcanır (doğrulamalardan sonra)
    await enforce_rate_limit(
        request, "deep", settings.rate_limit_deep_per_day, DAY_S, "deep_rate_limited"
    )

    options: dict[str, Any] = {"competitors": competitors, "use_reference": body.use_reference}
    if body.category:
        options["category"] = body.category
    scan = Scan(
        target_type=TargetType.WEB,
        target_label=parent.target_label,
        status=ScanStatus.QUEUED,
        phase=2,
        parent_scan_id=parent.id,
        compare_to_id=compare_to,
        options=options,
    )
    session.add(scan)
    await session.commit()
    try:
        await queue.enqueue_job(
            RUN_DEEP_SCAN_JOB, str(scan.id), _job_id=str(scan.id), _queue_name=DEEP_QUEUE
        )
    except Exception as exc:
        logger.error("derin tarama kuyruğa eklenemedi (%s)", type(exc).__name__)
        scan.status = ScanStatus.FAILED
        scan.error_code = "queue_unavailable"
        await session.commit()
        raise ApiError("queue_unavailable", 503) from exc
    info = await queue_info(session, scan, settings.deep_default_duration_s)
    return DeepScanCreated(
        scan_id=scan.id,
        queue_position=info.position if info else 1,
        eta_s=info.eta_s if info else settings.deep_default_duration_s,
    )


@router.get("/{scan_id}/events", response_model=EventsResponse)
async def get_events(
    scan_id: str,
    session: SessionDep,
    after: Annotated[int, Query(ge=0)] = 0,
    lang: Annotated[str | None, Query(max_length=16)] = None,
) -> EventsResponse:
    scan = await session.get(Scan, _uuid(scan_id))
    if scan is None:
        raise ApiError("not_found", 404)
    lang = normalize_lang(lang)
    rows = (
        await session.scalars(
            select(ScanEvent)
            .where(ScanEvent.scan_id == scan.id, ScanEvent.id > after)
            .order_by(ScanEvent.id)
            .limit(get_settings().deep_events_page_size)
        )
    ).all()
    return EventsResponse(
        events=[
            EventOut(
                id=e.id,
                module=e.module,
                text=event_text(e.key, lang, e.params),
                created_at=e.created_at,
            )
            for e in rows
        ],
        status=scan.status,
    )


@router.get("/{scan_id}/assets/{asset_id}")
async def get_asset(scan_id: str, asset_id: str, session: SessionDep) -> FileResponse:
    asset = await session.get(ScanAsset, _uuid(asset_id))
    # Başka bir taramanın asset'i istenirse de 404 (varlığı bile söylenmez)
    if asset is None or asset.scan_id != _uuid(scan_id):
        raise ApiError("not_found", 404)
    path = asset_store(get_settings()).resolve(asset.path)
    if path is None:
        raise ApiError("not_found", 404)
    return FileResponse(path, media_type="image/webp", headers=ASSET_HEADERS)
