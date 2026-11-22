"""Hız modülünün topladığı veri (PHASE2 §6.2). Check'ler sadece bunu okur.

Laboratuvar verisi Lighthouse'tan (mobil: simüle yavaş 4G + 4× CPU), saha verisi CrUX'tan
(gerçek Chrome kullanıcılarının son 28 günlük p75 değerleri) gelir. "Tahmini kazanç" değerleri
Lighthouse'un kendi hesabıdır; kendi tahminimizi üretmeyiz.
"""

import statistics
from dataclasses import dataclass, field
from typing import Any

MOBILE = "mobile"
DESKTOP = "desktop"


@dataclass(frozen=True)
class ResourceItem:
    """Lighthouse tablosundan sadeleştirilmiş satır (URL sorgu parametresiz)."""

    url: str
    total_bytes: int = 0
    wasted_bytes: int = 0
    third_party: bool = False


@dataclass(frozen=True)
class ImageItem:
    url: str
    total_bytes: int
    wasted_bytes: int
    reasons: tuple[str, ...]  # format | oversized | compression
    natural: tuple[int, int] | None = None  # dosyanın piksel boyutu
    displayed: tuple[int, int] | None = None  # sayfada gösterilen boyut


@dataclass(frozen=True)
class UncompressedItem:
    url: str
    resource_bytes: int
    transfer_bytes: int


@dataclass(frozen=True)
class CacheItem:
    url: str
    ttl_s: int
    total_bytes: int


@dataclass(frozen=True)
class ThirdParty:
    entity: str
    transfer_bytes: int
    main_thread_ms: float


@dataclass(frozen=True)
class Opportunity:
    audit: str
    savings_ms: float


@dataclass
class LighthouseRun:
    url: str
    form_factor: str  # mobile | desktop
    final_url: str = ""
    lcp_ms: float | None = None
    cls: float | None = None
    fcp_ms: float | None = None
    speed_index_ms: float | None = None
    tbt_ms: float | None = None
    # Boş ekran süresi: max(simüle FCP, filmstrip'te gözlenen ilk anlamlı kare) — alt sınır
    blank_ms: float | None = None
    scores: dict[str, float] = field(default_factory=dict)  # performance, seo, best-practices
    total_js_bytes: int = 0
    unused_js_bytes: int = 0
    unused_js_savings_ms: float = 0
    unused_js: list[ResourceItem] = field(default_factory=list)
    images: list[ImageItem] = field(default_factory=list)
    image_savings_bytes: int = 0
    image_savings_ms: float = 0
    unsized_images: list[str] = field(default_factory=list)
    uncompressed: list[UncompressedItem] = field(default_factory=list)
    short_cache: list[CacheItem] = field(default_factory=list)
    cache_savings_ms: float = 0
    third_parties: list[ThirdParty] = field(default_factory=list)
    # LCP'ye en çok kazanç sağlayacak Lighthouse önerileri (büyükten küçüğe)
    lcp_opportunities: list[Opportunity] = field(default_factory=list)
    # Filmstrip kareleri (gözlenen ms, JPEG) — sadece bellekte; asset'e dönüştürülür
    frames: list[tuple[float, bytes]] = field(default_factory=list, repr=False)


@dataclass(frozen=True)
class FieldData:
    """CrUX p75 değerleri. `scope`: url | origin; `form_factor`: phone | all."""

    scope: str
    form_factor: str
    lcp_ms: float | None = None
    cls: float | None = None
    inp_ms: float | None = None
    fcp_ms: float | None = None
    collection_period: dict[str, Any] | None = None


class FieldStatus:
    OK = "ok"
    NO_KEY = "no_key"  # LC_GOOGLE_API_KEY yok
    NO_DATA = "no_data"  # site CrUX'ta yok (yeterli trafik yok)
    QUOTA = "quota"  # ücretsiz kota doldu
    ERROR = "error"  # servis hatası / zaman aşımı


@dataclass
class PerfData:
    runs: list[LighthouseRun] = field(default_factory=list)
    field: FieldData | None = None
    field_status: str = FieldStatus.NO_KEY
    home_url: str = ""
    key_url: str | None = None

    def runs_for(self, url: str, form_factor: str = MOBILE) -> list[LighthouseRun]:
        return [r for r in self.runs if r.url == url and r.form_factor == form_factor]

    @property
    def mobile_urls(self) -> list[str]:
        return list(dict.fromkeys(r.url for r in self.runs if r.form_factor == MOBILE))

    def median_run(self, url: str, form_factor: str = MOBILE) -> LighthouseRun | None:
        """LCP'si ortanca olan ölçüm (öneriler ve tablolar bu ölçümden okunur)."""
        runs = sorted(
            (r for r in self.runs_for(url, form_factor) if r.lcp_ms is not None),
            key=lambda r: r.lcp_ms or 0,
        )
        if not runs:
            return None
        return runs[(len(runs) - 1) // 2]

    def median(self, url: str, metric: str, form_factor: str = MOBILE) -> float | None:
        values = [
            v for r in self.runs_for(url, form_factor) if (v := getattr(r, metric)) is not None
        ]
        return statistics.median(values) if values else None
