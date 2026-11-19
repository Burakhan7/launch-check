"""Veri modeli: scans ve findings. IP veya kullanıcı tanımlayıcı burada YOK."""

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from launchcheck.checks.base import Category, Severity, TargetType

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class ScanStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Verification(enum.StrEnum):
    """Faz 1 bulgusunun Faz 2'deki doğrulama sonucu."""

    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    ESCALATED = "escalated"
    NOT_VERIFIABLE = "not_verifiable"


def _enum(e: type[enum.StrEnum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


def utcnow() -> datetime:
    return datetime.now(UTC)


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
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # --- Faz 2 ---
    # Faz 2'nin dayandığı Faz 1 taraması
    parent_scan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Yeniden tarama farkı için önceki Faz 2
    compare_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scans.id", ondelete="SET NULL"), nullable=True
    )
    # {"modules": {"render": {"status": "done", "found": 7}}, "eta_s": 420}
    progress: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    # {"competitors": ["https://..."], "use_reference": true, "category": "saas"}
    options: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    # Worker'ın taramayı aldığı an (kuyruk beklemesi süreye ve takılma tespitine karışmasın)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    severity: Mapped[Severity] = mapped_column(_enum(Severity, "severity"))
    category: Mapped[Category] = mapped_column(_enum(Category, "category"))
    evidence: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    # --- Faz 2 ---
    source_phase: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # Sadece Faz 1 bulgularının Faz 2'deki kopyalarında dolu
    verification: Mapped[Verification | None] = mapped_column(
        _enum(Verification, "verification"), nullable=True
    )
    module: Mapped[str | None] = mapped_column(Text, nullable=True)

    scan: Mapped[Scan] = relationship(back_populates="findings")


class ScanEvent(Base):
    """Canlı ilerleme akışı. `key` i18n anahtarı; `params` URL dışında kişisel veri taşımaz."""

    __tablename__ = "scan_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    module: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class ScanAsset(Base):
    """Ekran görüntüsü vb. Dosya `assets_dir/<path>`'tedir; id tahmin edilemez (URL'de)."""

    __tablename__ = "scan_assets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(Text)
    page_url: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class ReferenceSiteStatus(enum.StrEnum):
    ACTIVE = "active"
    FAILED = "failed"
    ROBOTS_BLOCKED = "robots_blocked"
    NO_FIELD_DATA = "no_field_data"


class ReferenceSite(Base):
    """Kıyas için referans siteler (kullanıcı verisi değil, saklama süresi yok)."""

    __tablename__ = "reference_sites"
    __table_args__ = (UniqueConstraint("category", "origin", name="uq_reference_category_origin"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(Text, index=True)
    origin: Mapped[str] = mapped_column(Text)
    status: Mapped[ReferenceSiteStatus] = mapped_column(
        _enum(ReferenceSiteStatus, "reference_site_status"), default=ReferenceSiteStatus.ACTIVE
    )
    popularity: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    anatomy: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
