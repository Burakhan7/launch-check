"""DeepContext: Faz 2 modüllerinin ve check'lerinin okuduğu bağlam."""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from launchcheck.checks.base import Category, ScanContext, Severity
from launchcheck.collectors.web_collector import WebContext
from launchcheck.deep.assets import AssetStore
from launchcheck.deep.events import EventSink, MemoryEventSink
from launchcheck.deep.perf_data import PerfData
from launchcheck.deep.render_data import RenderData


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
    # Faz 1 WebContext'i (render modülü doldurur); Faz 2 check'leri `ctx.base` ile okur
    base: WebContext | None = None
    # Render modülünün topladığı veri (tarayıcıdan bağımsız; check'ler bunu okur)
    render: RenderData | None = None
    # Hız modülünün topladığı veri (Lighthouse + CrUX)
    perf: PerfData | None = None
    # Şeritler arası: render modülü kilit sayfaları (fiyat > kayıt > ürün) bulunca doldurur;
    # render bitince (başarısız olsa bile) olay işaretlenir, hız şeridi sonsuza dek beklemez.
    key_urls: list[str] = field(default_factory=list)
    key_urls_ready: asyncio.Event = field(default_factory=asyncio.Event)

    def note_checks(self, key: str, check_ids: list[str]) -> None:
        """`checks_failed` / `checks_skipped` listelerine ekler (modüller paralel yazar).
        Atlanan check'ler (ör. saha verisi yokken INP) raporda "geçti" sayılmaz."""
        existing = self.meta.get(key)
        if existing is None:
            existing = list(self.parent_meta.get(key, [])) if key == "checks_failed" else []
        self.meta[key] = list(dict.fromkeys([*existing, *check_ids]))

    def set_key_urls(self, urls: list[str]) -> None:
        self.key_urls = list(urls)
        self.key_urls_ready.set()

    async def aclose(self) -> None:
        if self.base is not None:
            await self.base.aclose()
