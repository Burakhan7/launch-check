"""Veri modeli: scans ve findings. IP veya kullanıcı tanımlayıcı burada YOK."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class TargetType(enum.StrEnum):
    WEB = "web"
    APK = "apk"


class ScanStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class SeverityDb(enum.StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class CategoryDb(enum.StrEnum):
    SECURITY = "security"
    PROD_READINESS = "prod_readiness"
    PERFORMANCE = "performance"
    CONVERSION = "conversion"
    STORE_COMPLIANCE = "store_compliance"


def _enum(e: type[enum.StrEnum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_type: Mapped[TargetType] = mapped_column(_enum(TargetType, "target_type"))
    target_label: Mapped[str] = mapped_column(Text)
    status: Mapped[ScanStatus] = mapped_column(
        _enum(ScanStatus, "scan_status"), default=ScanStatus.QUEUED
    )
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category_scores: Mapped[dict[str, int] | None] = mapped_column(JsonType, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    phase: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    findings: Mapped[list["FindingRow"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", passive_deletes=True
    )


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True
    )
    check_id: Mapped[str] = mapped_column(Text)
    severity: Mapped[SeverityDb] = mapped_column(_enum(SeverityDb, "severity"))
    category: Mapped[CategoryDb] = mapped_column(_enum(CategoryDb, "category"))
    evidence: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    scan: Mapped[Scan] = relationship(back_populates="findings")
