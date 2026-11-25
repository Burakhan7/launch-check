"""Gizlilik modülünün topladığı veri (PHASE2 §6.5). Tarayıcıdan bağımsız: check'ler bunu okur.

Kişisel veri ilkesi: çerez DEĞERLERİ ve istek gövdeleri yok; sadece çerez adı/alan adı, istek
host'u ve sorgu dizesiz yol. Script gövdeleri sadece sürüm tespiti için bellekte kalır, saklanmaz.
"""

from dataclasses import dataclass, field
from typing import Any


class PartStatus:
    OK = "ok"
    UNAVAILABLE = "unavailable"  # tarayıcı/veri dosyası yok, sayfa açılamadı
    NOT_APPLICABLE = "not_applicable"  # ör. banner yok → "Reddet" denenemez
    TIMEOUT = "timeout"
    PENDING = "pending"


PART_CHECKS: dict[str, tuple[str, ...]] = {
    "consent": ("web.privacy.trackers_before_consent", "web.privacy.consent_banner_missing"),
    # "Reddet" tıklanamadıysa (banner yok, reddet seçeneği yok) bu check atlanır
    "reject": ("web.privacy.trackers_after_reject",),
    "libraries": ("web.security.vulnerable_js_library",),
    "scripts": ("web.security.risky_third_party_script",),
    "axe": ("web.a11y.axe_violations",),
}


class RejectState:
    CLICKED = "clicked"  # ilk katmanda "Reddet / Sadece gerekli"
    CLICKED_SECOND_LAYER = "clicked_second_layer"  # "Ayarlar" açıldıktan sonra
    NOT_FOUND = "not_found"  # banner var ama reddetme seçeneği bulunamadı
    NO_BANNER = "no_banner"
    FAILED = "failed"  # tıklama başarısız / sayfa yeniden açılamadı


@dataclass(frozen=True)
class TrackerHit:
    name: str  # okunur ad (elle tutulan liste) veya alan adı (EasyPrivacy)
    domain: str
    category: str
    requests: int
    cookies: tuple[str, ...] = ()  # bu takipçiye ait çerez ADLARI


@dataclass
class PhaseResult:
    """Bir aşamada (onaysız / reddettikten sonra) görülen takip."""

    trackers: list[TrackerHit] = field(default_factory=list)
    requests: int = 0  # toplam istek sayısı
    # Google Consent Mode "reddedildi" durumundaki çerezsiz ölçüm istekleri (gcs=G100):
    # takip sayılmaz, kanıtta gösterilir
    consent_mode_pings: int = 0
    tag_managers: list[str] = field(default_factory=list)  # sadece yükleyici (GTM)

    @property
    def tracking(self) -> bool:
        return bool(self.trackers)


@dataclass
class ConsentInfo:
    cmp: str | None = None  # tanınan onay yönetim aracı
    banner: bool = False  # görünür bir çerez banner'ı/diyaloğu var
    reject: str = RejectState.NO_BANNER
    reject_label: str = ""  # tıklanan düğmenin kısaltılmış etiketi
    banner_after_reject: bool = False  # yeniden yüklemede banner tekrar çıktı (tercih saklanmadı)


@dataclass(frozen=True)
class LibraryFinding:
    library: str
    version: str
    url: str  # sorgu dizesiz
    severity: str  # en yüksek açık önemi (retire)
    vulnerabilities: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class RiskyScript:
    url: str
    domain: str
    source: str


@dataclass(frozen=True)
class AxeViolation:
    rule: str
    impact: str  # serious | critical
    help: str
    help_url: str
    count: int  # toplam etkilenen öğe
    pages: tuple[str, ...]
    viewports: tuple[str, ...]
    targets: tuple[str, ...]  # örnek seçiciler (en fazla 3)


@dataclass
class PrivacyData:
    site: str
    status: dict[str, str] = field(
        default_factory=lambda: dict.fromkeys(PART_CHECKS, PartStatus.PENDING)
    )
    consent: ConsentInfo = field(default_factory=ConsentInfo)
    before: PhaseResult | None = None
    after: PhaseResult | None = None
    script_urls: list[str] = field(default_factory=list)  # tüm sayfalarda yüklenen script'ler
    scripts_scanned: int = 0
    libraries: list[LibraryFinding] = field(default_factory=list)
    risky: list[RiskyScript] = field(default_factory=list)
    axe: list[AxeViolation] = field(default_factory=list)
    axe_runs: int = 0
    retire_db_version: str = ""

    def ok(self, part: str) -> bool:
        return self.status.get(part) == PartStatus.OK

    def skipped_checks(self) -> list[str]:
        return [c for part, ids in PART_CHECKS.items() if not self.ok(part) for c in ids]

    def summary(self) -> dict[str, Any]:
        def names(phase: PhaseResult | None) -> list[str]:
            return [t.name for t in phase.trackers] if phase else []

        return {
            "status": dict(self.status),
            "cmp": self.consent.cmp,
            "banner": self.consent.banner,
            "reject": self.consent.reject,
            "trackers_before": names(self.before),
            "trackers_after": names(self.after),
            "scripts": len(self.script_urls),
            "scripts_scanned": self.scripts_scanned,
            "axe_runs": self.axe_runs,
        }
