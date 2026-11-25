"""Huni yürüyüşü verisi (PHASE2 §6.6, §8.6). Tarayıcıdan bağımsız: check'ler bunu okur.

Kişisel veri ilkesi: form alanlarının DEĞERLERİ hiç okunmaz; sadece tür, `autocomplete`,
`inputmode`, zorunluluk ve kısaltılmış etiket. Sayfa metni sadece anahtar kelime için kullanılır,
saklanmaz.
"""

from dataclasses import dataclass, field
from typing import Any


class PartStatus:
    OK = "ok"
    NO_DATA = "no_data"  # kayıt formu / ücretli plan sinyali bulunamadı
    UNAVAILABLE = "unavailable"
    PENDING = "pending"


PART_CHECKS: dict[str, tuple[str, ...]] = {
    "signup": ("web.funnel.signup_friction", "web.funnel.mobile_input_types"),
    "path": ("web.funnel.too_many_steps",),
    "pricing": ("web.funnel.price_hidden", "web.funnel.vat_excluded_price"),
}
# Ücretli plan sinyali veya fiyat yoksa fiyat check'lerinin kontrol edeceği bir şey yoktur:
# "geçti" sayılır. Kayıt formu bulunamazsa ise check'ler atlanır (form JS ile/girişten sonra
# açılıyor olabilir; "sorun yok" demek yanlış olur).
NO_DATA_IS_PASS = frozenset({"pricing"})


@dataclass(frozen=True)
class FormField:
    kind: str  # email | tel | password | name | card | date | checkbox | select | text | other
    type: str
    inputmode: str
    autocomplete: str
    required: bool
    label: str  # kısaltılmış etiket (değer değil)


@dataclass
class SignupForm:
    page: str  # yol
    fields: list[FormField] = field(default_factory=list)
    social: list[str] = field(default_factory=list)  # google, apple...
    card: bool = False  # kart alanı veya ödeme iframe'i
    consent_checkbox: bool = False  # KVKK/şartlar onay kutusu
    in_dialog: bool = False

    @property
    def data_fields(self) -> list[FormField]:
        return [f for f in self.fields if f.kind not in ("checkbox",)]

    @property
    def required_count(self) -> int:
        return sum(1 for f in self.data_fields if f.required)


@dataclass
class PricingInfo:
    page: str
    prices: int  # fiyat içeren ayrı kapsayıcı sayısı (≈ plan/ürün kartı)
    currencies: list[str] = field(default_factory=list)
    vat_excluded: list[str] = field(default_factory=list)  # eşleşen ifadeler
    free_tier: bool = False
    periods: list[str] = field(default_factory=list)  # monthly | yearly
    quote_based: bool = False  # "teklif al / satış ekibiyle görüşün"


@dataclass
class FunnelStep:
    kind: str  # home | cta | login | signup | pricing
    url: str
    clicks: int  # ana sayfadan bu adıma tık sayısı
    load_ms: int | None
    via: str  # cta | link | direct
    asset_id: str | None = None  # mobil ekran görüntüsü
    issues: list[str] = field(default_factory=list)  # rapor: bu adımdaki bulgu check_id'leri


@dataclass
class FunnelData:
    site: str
    status: dict[str, str] = field(
        default_factory=lambda: dict.fromkeys(PART_CHECKS, PartStatus.PENDING)
    )
    steps: list[FunnelStep] = field(default_factory=list)
    cta_text: str = ""
    signup: SignupForm | None = None
    signup_clicks: int | None = None
    pricing: list[PricingInfo] = field(default_factory=list)  # ana sayfa ve/veya fiyat sayfası
    paid_signals: list[str] = field(default_factory=list)
    price_distance: int | None = None  # fiyat görünen en yakın sayfaya tık; None → hiç yok
    pricing_page: str | None = None

    def done(self, part: str) -> bool:
        state = self.status.get(part)
        return state == PartStatus.OK or (state == PartStatus.NO_DATA and part in NO_DATA_IS_PASS)

    def skipped_checks(self) -> list[str]:
        return [c for part, ids in PART_CHECKS.items() if not self.done(part) for c in ids]

    def summary(self) -> dict[str, Any]:
        """Huni haritası (PHASE2 §8.6): rapor bu yapıyı çizer."""
        signup = self.signup
        return {
            "status": dict(self.status),
            "cta": self.cta_text,
            "steps": [
                {
                    "kind": s.kind,
                    "url": s.url,
                    "clicks": s.clicks,
                    "load_ms": s.load_ms,
                    "via": s.via,
                    "asset_id": s.asset_id,
                    "issues": s.issues,
                }
                for s in self.steps
            ],
            "signup": None
            if signup is None
            else {
                "page": signup.page,
                "fields": len(signup.data_fields),
                "required": signup.required_count,
                "social": signup.social,
                "card": signup.card,
                "consent_checkbox": signup.consent_checkbox,
            },
            "signup_clicks": self.signup_clicks,
            "price_distance": self.price_distance,
            "pricing_page": self.pricing_page,
            "pricing": [
                {
                    "page": p.page,
                    "prices": p.prices,
                    "currencies": p.currencies,
                    "vat_excluded": bool(p.vat_excluded),
                    "free_tier": p.free_tier,
                    "periods": p.periods,
                }
                for p in self.pricing
            ],
        }
