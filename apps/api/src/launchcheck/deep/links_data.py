"""Link modülünün topladığı veri (PHASE2 §6.4). Ağdan bağımsız: check'ler sadece bunu okur.

Kişisel veri ilkesi: link adreslerinde sorgu dizesi tutulmaz (UTM, oturum token'ı olabilir);
yanıt gövdeleri saklanmaz, sadece durum kodu, içerik türü ve boyut.
"""

from dataclasses import dataclass, field
from typing import Any


class LinkOutcome:
    OK = "ok"
    BROKEN = "broken"
    UNKNOWN = "unknown"  # bot engeli, giriş gerektiren sayfa, hız sınırı, zaman aşımı


class PartStatus:
    OK = "ok"
    NO_DATA = "no_data"  # kontrol edilecek bir şey yok (ör. sayfada video yok)
    UNAVAILABLE = "unavailable"  # dış servis / ağ hatası
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PENDING = "pending"


# Parça → o parçanın verisini okuyan check'ler (parça tamamlanmazsa check atlanır)
PART_CHECKS: dict[str, tuple[str, ...]] = {
    "internal": ("web.links.broken_internal",),
    "external": ("web.links.broken_external",),
    "https_targets": ("web.links.http_target_no_https",),
    "youtube": ("web.links.youtube_unavailable",),
    "og_image": ("web.meta.og_image_unreachable",),
    "structured": ("web.seo.structured_data_invalid",),
    # Fırsat sinyalleri render ister (fiyat, soru başlıkları, harita); Faz 1 HTML'i yetmez
    "sd_opportunity": ("web.seo.structured_data_opportunity",),
}
# Verisi yoksa (NO_DATA) "geçti" sayılabilecek parçalar: kontrol edilecek bir şey olmaması
# sorun olmaması demektir (ör. sayfada video yok → bozuk video yok).
NO_DATA_IS_PASS = frozenset({"internal", "external", "https_targets", "youtube", "structured"})


@dataclass
class LinkStatus:
    url: str  # sorgu dizesi ve # olmadan
    outcome: str
    status: int | None = None
    reason: str = ""  # http_404, http_410, http_5xx, dns, connect, tls, timeout, auth, blocked...
    pages: list[str] = field(default_factory=list)  # bulunduğu sayfaların yolları
    prominent: bool = False  # menü/header/footer'da


@dataclass(frozen=True)
class HttpsTarget:
    """Faz 1'in `insecure_http_links` bulgusundaki bir http:// hedefinin HTTPS desteği."""

    host: str
    url: str  # denenen https:// adresi
    supports_https: bool | None  # None → karar verilemedi (zaman aşımı, bot engeli)
    reason: str = ""  # tls, connect, dns, timeout, http_<kod>


@dataclass(frozen=True)
class VideoStatus:
    video_id: str
    url: str
    page: str
    embedded: bool  # sayfada oynatılıyor (iframe); sadece linkse gömme kapalı olması sorun değil
    state: str  # ok | removed | not_embeddable | unknown
    http_status: int | None = None


@dataclass(frozen=True)
class OgImage:
    page: str
    tag: str  # og:image | twitter:image
    url: str
    source: str  # "server" (paylaşım botlarının gördüğü HTML) | "rendered"
    status: int | None
    content_type: str = ""
    bytes: int | None = None  # None → bilinmiyor
    width: int | None = None
    height: int | None = None
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class JsonLdIssue:
    kind: str  # parse_error | missing_context | missing_type | missing_field
    type: str = ""  # schema.org türü (Product, FAQPage...)
    field: str = ""  # eksik alan
    detail: str = ""  # kısa (parse hatası konumu vb.), sayfa içeriği değil


@dataclass
class JsonLdPage:
    page: str
    blocks: int
    types: list[str] = field(default_factory=list)
    issues: list[JsonLdIssue] = field(default_factory=list)


@dataclass(frozen=True)
class SdOpportunity:
    kind: str  # product | faq | local_business
    page: str
    signals: list[str]  # hangi sinyaller görüldü (ör. "prices:6", "add_to_cart")


@dataclass
class LinksData:
    site: str  # hedefin origin'i
    source: str = "render"  # linklerin kaynağı: "render" | "phase1" (render verisi yoksa)
    status: dict[str, str] = field(
        default_factory=lambda: dict.fromkeys(PART_CHECKS, PartStatus.PENDING)
    )
    internal: list[LinkStatus] = field(default_factory=list)
    internal_total: int = 0  # bulunan tekil iç link sayısı
    internal_checked: int = 0
    # SPA rotası sunucuda 404 dönüyor ama tıklayınca açılıyorsa (render'ın spa_deep_link_404
    # bulgusu) diğer 404'ler de aynı sebepten olabilir: kırık link sayılmaz
    spa_router_404s_excluded: bool = False
    external: list[LinkStatus] = field(default_factory=list)
    external_total: int = 0
    external_checked: int = 0
    https_targets: list[HttpsTarget] = field(default_factory=list)
    videos: list[VideoStatus] = field(default_factory=list)
    og_images: list[OgImage] = field(default_factory=list)
    jsonld: list[JsonLdPage] = field(default_factory=list)
    opportunities: list[SdOpportunity] = field(default_factory=list)
    present_types: list[str] = field(default_factory=list)  # sitede bulunan tüm SD türleri

    def ok(self, part: str) -> bool:
        return self.status.get(part) == PartStatus.OK

    def done(self, part: str) -> bool:
        state = self.status.get(part)
        return state == PartStatus.OK or (state == PartStatus.NO_DATA and part in NO_DATA_IS_PASS)

    def skipped_checks(self) -> list[str]:
        return [c for part, ids in PART_CHECKS.items() if not self.done(part) for c in ids]

    def summary(self) -> dict[str, Any]:
        """Rapor için özet (`scan.meta.links`): sayılar ve parça durumları."""
        return {
            "source": self.source,
            "status": dict(self.status),
            "internal_total": self.internal_total,
            "internal_checked": self.internal_checked,
            "external_total": self.external_total,
            "external_checked": self.external_checked,
            "videos_checked": len(self.videos),
            "og_images_checked": len(self.og_images),
            "jsonld_pages": len([p for p in self.jsonld if p.blocks]),
            "structured_types": self.present_types[:20],
        }
