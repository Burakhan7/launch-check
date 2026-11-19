"""Faz 2 testleri için DB yardımcıları."""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.checks.base import Category, Severity, TargetType
from launchcheck.db.models import FindingRow, Scan, ScanStatus, utcnow

SITE = "https://example.com"


async def add_parent(
    sm: async_sessionmaker[AsyncSession],
    *,
    age: timedelta = timedelta(minutes=5),
    status: ScanStatus = ScanStatus.DONE,
    target_type: TargetType = TargetType.WEB,
    meta: dict[str, Any] | None = None,
    findings: list[tuple[str, Severity, Category]] | None = None,
) -> uuid.UUID:
    async with sm() as s:
        scan = Scan(
            target_type=target_type,
            target_label=SITE,
            status=status,
            score=80,
            meta=meta if meta is not None else {"platform": ["vite"], "checks_failed": []},
            created_at=utcnow() - age,
        )
        s.add(scan)
        await s.flush()
        for check_id, severity, category in findings or []:
            s.add(
                FindingRow(
                    scan_id=scan.id,
                    check_id=check_id,
                    severity=severity,
                    category=category,
                    evidence={"path": "/x"},
                )
            )
        await s.commit()
        return scan.id


async def add_deep(
    sm: async_sessionmaker[AsyncSession],
    parent_id: uuid.UUID | None,
    *,
    status: ScanStatus = ScanStatus.QUEUED,
    age: timedelta = timedelta(0),
    started_ago: timedelta | None = None,
) -> uuid.UUID:
    async with sm() as s:
        scan = Scan(
            target_type=TargetType.WEB,
            target_label=SITE,
            status=status,
            phase=2,
            parent_scan_id=parent_id,
            options={"competitors": [], "use_reference": True},
            created_at=utcnow() - age,
            started_at=utcnow() - started_ago if started_ago is not None else None,
        )
        s.add(scan)
        await s.commit()
        return scan.id
