"""Render modülünün topladığı veri. Check'ler sadece bunu okur (tarayıcıya bağımlı değil).

Kişisel veri ilkesi: storage ve çerez DEĞERLERİ burada yok; sadece ad, uzunluk, bayraklar ve
JWT ise bitiş süresi. Console metinleri kısaltılır; ağ kayıtlarında sorgu dizesi tutulmaz.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Box:
    """Görünür bir öğe: tür, kısa metin ve viewport koordinatları (CSS px)."""

    tag: str
    text: str
    x: float
    y: float
    w: float
    h: float
    selector: str = ""

    @classmethod
    def from_js(cls, data: dict[str, Any]) -> "Box":
        return cls(
            tag=str(data.get("tag", "")),
            text=str(data.get("text", ""))[:80],
            x=float(data.get("x", 0)),
            y=float(data.get("y", 0)),
            w=float(data.get("w", 0)),
            h=float(data.get("h", 0)),
            selector=str(data.get("selector", ""))[:120],
        )

    def as_evidence(self) -> dict[str, Any]:
        return {
            "element": self.selector or self.tag,
            "text": self.text,
            "box": [round(self.x), round(self.y), round(self.w), round(self.h)],
        }


@dataclass(frozen=True)
class FirstScreenItem:
    """İlk ekran özeti (PHASE2 §10.3): LLM ve anatomi görsel yerine bunu kullanır."""

    kind: str  # heading, paragraph, button, link, image, video, form, logo_grid
    text: str
    position: str  # top | middle | bottom
    size_pct: float  # viewport alanının yüzdesi
    primary_cta: bool = False


@dataclass(frozen=True)
class ViewportMetrics:
    width: int
    height: int
    inner_width: int  # sayfanın gördüğü genişlik (viewport etiketi yoksa mobilde 980)
    scroll_width: int
    overflowing: list[Box] = field(default_factory=list)
    overlaps: list[tuple[Box, Box]] = field(default_factory=list)
    small_targets: list[Box] = field(default_factory=list)
    primary_cta: Box | None = None
    first_screen: list[FirstScreenItem] = field(default_factory=list)

    @property
    def overflow_px(self) -> int:
        return max(0, self.scroll_width - self.inner_width)


@dataclass(frozen=True)
class ConsoleError:
    message: str  # kısaltılmış, tekilleştirme anahtarı
    kind: str  # "exception" (yakalanmamış) | "console"


@dataclass(frozen=True)
class NetworkFailure:
    url: str  # sorgu dizesiz
    resource_type: str  # document, script, fetch, xhr, image...
    status: int | None  # HTTP durumu; ağ hatasında None
    error: str  # "http_4xx", "http_5xx", "cors", "network"


@dataclass(frozen=True)
class BrokenImage:
    src: str
    hero: bool


@dataclass(frozen=True)
class JunkText:
    token: str
    snippet: str


@dataclass(frozen=True)
class StorageEntry:
    area: str  # local | session
    key: str
    length: int
    jwt: bool
    jwt_exp_days: float | None  # şu andan itibaren kalan gün (JWT ve exp varsa)


@dataclass(frozen=True)
class CookieInfo:
    name: str
    domain: str
    secure: bool
    http_only: bool
    same_site: str  # Strict | Lax | None
    session: bool  # tarayıcı kapanınca silinir mi


@dataclass(frozen=True)
class SdSignals:
    """Yapılandırılmış veri fırsatı için sayfa sinyalleri (metin kısaltılmış, kişisel veri yok)."""

    headings: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)  # menü dışı düğme/link etiketleri
    questions: int = 0  # soru işaretiyle biten başlık/summary/dt sayısı
    accordions: int = 0
    prices: int = 0  # görünür fiyat metni sayısı
    tel: int = 0
    map: bool = False
    address: bool = False

    @classmethod
    def from_js(cls, data: Any) -> "SdSignals":
        if not isinstance(data, dict):
            return cls()
        return cls(
            headings=[str(h)[:80] for h in data.get("headings") or []][:40],
            actions=[str(a)[:80] for a in data.get("actions") or []][:60],
            questions=int(data.get("questions") or 0),
            accordions=int(data.get("accordions") or 0),
            prices=int(data.get("prices") or 0),
            tel=int(data.get("tel") or 0),
            map=bool(data.get("map")),
            address=bool(data.get("address")),
        )


@dataclass
class RenderedPage:
    url: str
    final_url: str
    status: int | None
    title: str = ""
    h1: str = ""
    canonical: str = ""
    og: dict[str, str] = field(default_factory=dict)
    main_words: int = 0  # render sonrası ana içerik kelime sayısı (nav/footer hariç)
    internal_links: list[str] = field(default_factory=list)
    priority_links: list[str] = field(default_factory=list)  # nav/header/footer
    # Başka origin'e giden linkler (sorgu dizesiz) → "chrome" (menü/footer) | "content"
    external_links: dict[str, str] = field(default_factory=dict)
    console_errors: list[ConsoleError] = field(default_factory=list)
    failures: list[NetworkFailure] = field(default_factory=list)
    api_calls: list[tuple[str, str]] = field(default_factory=list)  # (yöntem, sorgu dizesiz URL)
    broken_images: list[BrokenImage] = field(default_factory=list)
    junk: list[JunkText] = field(default_factory=list)
    mobile: ViewportMetrics | None = None
    desktop: ViewportMetrics | None = None
    widths: dict[int, ViewportMetrics] = field(default_factory=dict)
    is_funnel: bool = False  # fiyat/kayıt sayfası (hata önemi yükselir)
    has_password_field: bool = False
    admin_signals: list[str] = field(default_factory=list)
    bot_challenge: bool = False  # bot doğrulama sayfası geldi (taranmaz)
    jsonld: list[str] = field(default_factory=list)  # ham JSON-LD blokları (render sonrası DOM)
    microdata: list[str] = field(default_factory=list)  # itemtype değerleri
    # YouTube gömme/link adresi → sayfada gömülü (iframe) mü
    videos: dict[str, bool] = field(default_factory=dict)
    sd: SdSignals = field(default_factory=SdSignals)


@dataclass(frozen=True)
class DeepLinkProbe:
    url: str
    direct_status: int | None
    via_click_ok: bool


@dataclass(frozen=True)
class NotFoundProbe:
    path: str
    status: int | None
    words: int
    has_home_link: bool
    default_page: str | None  # tanınan varsayılan 404 sayfası (nginx, vercel...)


@dataclass(frozen=True)
class OfflineProbe:
    url: str
    outcome: str  # blank | spinner | message | browser_error_page | not_spa
    words: int


@dataclass(frozen=True)
class AdminProbe:
    path: str
    status: int | None
    signals: list[str]  # admin_heading, data_table, destructive_buttons
    login_form: bool
    redirected_to_login: bool


@dataclass
class RenderData:
    pages: list[RenderedPage] = field(default_factory=list)
    storage: list[StorageEntry] = field(default_factory=list)
    cookies: list[CookieInfo] = field(default_factory=list)
    deep_links: list[DeepLinkProbe] = field(default_factory=list)
    not_found: NotFoundProbe | None = None
    offline: OfflineProbe | None = None
    admin: list[AdminProbe] = field(default_factory=list)
    sourcemap_sources: list[str] = field(default_factory=list)
    sourcemap_source_count: int = 0
    # Proxy'nin engellediği hedefler (host, port, neden): localhost'a gerçekten istek atıldı mı?
    blocked_hosts: list[tuple[str, int, str]] = field(default_factory=list)

    @property
    def home(self) -> RenderedPage | None:
        return self.pages[0] if self.pages else None
