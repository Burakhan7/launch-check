"""web.conversion.value_proposition için girdi/çıktı şemaları ve değerlendirme rubriği.

Faz 2'de bir LLM (Gemini) bu girdiyle çağrılacak ve çıktıyı bu şemaya uygun JSON olarak
döndürecek. Şema doğrulamasından geçmeyen çıktı bulguya dönüştürülmez.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class RubricItem(StrEnum):
    WHAT = "what"  # Ürünün ne yaptığı ilk ekranda 1 cümlede anlaşılıyor mu
    WHO = "who"  # Hedef kitle belli mi
    WHY = "why"  # Neden şimdi / neden bu: alternatife göre fark söyleniyor mu
    TRUST = "trust"  # Arkasında kim var, veri nerede, sosyal kanıt
    SINGLE_ACTION = "single_action"  # İlk ekranda tek ve net birincil CTA
    PRICING = "pricing"  # Ücretli plan varsa fiyat 1 tıkla ve sunucu HTML'inde
    CONSISTENCY = "consistency"  # Marka/şirket bilgisi, iddialar, sayılar tutarlı mı


RUBRIC: dict[RubricItem, str] = {
    RubricItem.WHAT: "Ürünün ne yaptığı ilk ekranda tek cümlede anlaşılıyor mu?",
    RubricItem.WHO: "Hedef kitle belli mi?",
    RubricItem.WHY: (
        "Rakiplere veya mevcut alternatife göre farkı söyleniyor mu? Meta keywords veya title'da "
        'rakip/alternatif sinyali ("alternatif", "alternative", "vs", bir rakip marka) var '
        "ama görünür metinde bu fark hiç anlatılmıyorsa `positioning_hidden_in_meta` işaretle."
    ),
    RubricItem.TRUST: (
        "Arkasında kim olduğu (şirket/kişi), verinin nerede tutulduğu ve sosyal kanıt var mı?"
    ),
    RubricItem.SINGLE_ACTION: "İlk ekranda tek ve net bir birincil CTA var mı?",
    RubricItem.PRICING: (
        "Ücretli plan varsa fiyat ana sayfadan 1 tıkla ve sunucu HTML'inde görülebiliyor mu?"
    ),
    RubricItem.CONSISTENCY: (
        "Marka/şirket bilgisi, iddialar ve sayılar sayfalar arasında çelişiyor mu?"
    ),
}
MAX_SCORE = 2  # her rubrik maddesi 0-2


class CtaLink(BaseModel):
    text: str
    target: str  # mutlak veya göreli adres


class ValuePropositionInput(BaseModel):
    """LLM'e verilen girdi: SADECE ana sayfadan çıkarılmış metinler ve meta bayrakları."""

    url: str
    title: str = ""
    meta_description: str = ""
    meta_keywords: list[str] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)  # h1-h3, sırayla
    hero_text: str = ""  # ilk ekran metni
    ctas: list[CtaLink] = Field(default_factory=list)
    nav_links: list[str] = Field(default_factory=list)
    footer_links: list[str] = Field(default_factory=list)
    flags: dict[str, bool | list[str]] = Field(default_factory=dict)


class RubricScore(BaseModel):
    score: int = Field(ge=0, le=MAX_SCORE)
    reason: str
    suggestion: str


class LlmFinding(BaseModel):
    rubric_item: RubricItem
    severity: Literal["high", "medium", "low", "info"]
    evidence_text: str  # girdide AYNEN geçen metin (uydurma kontrolü için)
    suggestion: str
    rewrite_example: str = ""  # önerilen başlık/alt başlık örneği
    needs_verification: bool = False  # piyasa/rakip bağlamı gerekiyorsa
    positioning_hidden_in_meta: bool = False


class ValuePropositionOutput(BaseModel):
    scores: dict[RubricItem, RubricScore]
    findings: list[LlmFinding] = Field(default_factory=list)
