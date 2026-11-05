import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from launchcheck.api.deps import get_url_guard, rate_limit
from launchcheck.api.errors import ApiError
from launchcheck.api.queue import RUN_SCAN_JOB, JobQueue, get_queue
from launchcheck.api.report import build_report
from launchcheck.api.schemas import ScanCreated, ScanReport, WebScanRequest
from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.config import get_settings
from launchcheck.db.models import FindingRow, Scan, ScanStatus
from launchcheck.db.session import get_session
from launchcheck.i18n import normalize_lang
from launchcheck.security.ssrf import UrlGuard
from launchcheck.security.url import TargetValidationError, normalize_target_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scans", tags=["scans"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
GuardDep = Annotated[UrlGuard, Depends(get_url_guard)]
web_rate_limit = rate_limit("web", lambda: get_settings().rate_limit_web_per_hour)


async def _enqueue(
    session: AsyncSession, queue: JobQueue, scan: Scan, args: dict[str, Any]
) -> None:
    try:
        await queue.enqueue_job(RUN_SCAN_JOB, str(scan.id), args, _job_id=str(scan.id))
    except Exception as exc:
        logger.error("kuyruğa eklenemedi (%s)", type(exc).__name__)
        scan.status = ScanStatus.FAILED
        scan.error_code = "queue_unavailable"
        await session.commit()
        raise ApiError("queue_unavailable", 503) from exc


@router.post(
    "/web", status_code=202, response_model=ScanCreated, dependencies=[Depends(web_rate_limit)]
)
async def create_web_scan(
    body: WebScanRequest, session: SessionDep, queue: QueueDep, guard: GuardDep
) -> ScanCreated:
    try:
        target = normalize_target_url(body.url)
        # Erken geri bildirim için DNS + IP kontrolü. Worker bağlantı anında tekrar doğrular.
        await guard.resolve_public(target.host, target.port)
    except TargetValidationError as exc:
        raise ApiError(exc.code, 400) from exc

    scan = Scan(target_type=TargetType.WEB, target_label=target.origin, status=ScanStatus.QUEUED)
    session.add(scan)
    await session.commit()
    # Tam URL DB'ye yazılmaz, sadece job argümanı olarak kuyruğa gider.
    await _enqueue(session, queue, scan, {"url": target.url})
    return ScanCreated(scan_id=scan.id)


@router.get("/{scan_id}", response_model=ScanReport)
async def get_scan(
    scan_id: str,
    session: SessionDep,
    lang: Annotated[str | None, Query(max_length=16)] = None,
) -> ScanReport:
    try:
        parsed_id = uuid.UUID(scan_id)
    except ValueError as exc:
        raise ApiError("not_found", 404) from exc

    scan = await session.get(Scan, parsed_id)
    if scan is None:
        raise ApiError("not_found", 404)
    rows = list(
        (await session.scalars(select(FindingRow).where(FindingRow.scan_id == scan.id))).all()
    )
    return build_report(
        scan,
        rows,
        registry=load_builtin_checks(),
        lang=normalize_lang(lang),
        retention_days=get_settings().report_retention_days,
    )
