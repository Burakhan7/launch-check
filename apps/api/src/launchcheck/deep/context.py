"""DeepContext: Faz 2 modüllerinin ve check'lerinin okuduğu bağlam."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from launchcheck.checks.base import Category, ScanContext, Severity
from launchcheck.collectors.web_collector import WebContext
from launchcheck.deep.assets import AssetStore
from launchcheck.deep.events import EventSink, MemoryEventSink


@dataclass(frozen=True)
class ParentFinding:
    """Faz 1 bulgusu (DB'deki hâliyle; özel nitelikli veri yükseltmesi uygulanmış)."""

    check_id: str
    severity: Severity
    category: Category
    evidence: dict[str, Any]


@dataclass
class DeepContext(ScanContext):
    scan_id: uuid.UUID = field(default_factory=uuid.uuid4)
    parent_scan_id: uuid.UUID | None = None
    # Taranacak adres: Faz 1'in normalize origin'i (Faz 1 tam URL'yi DB'ye yazmaz)
    target_url: str = ""
    parent_meta: dict[str, Any] = field(default_factory=dict)
    parent_findings: list[ParentFinding] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    events: EventSink = field(default_factory=MemoryEventSink)
    assets: AssetStore | None = None
    # Faz 1 WebContext'i (render modülü P3'te doldurur); Faz 2 check'leri `ctx.base` ile okur
    base: WebContext | None = None

    async def aclose(self) -> None:
        if self.base is not None:
            await self.base.aclose()
