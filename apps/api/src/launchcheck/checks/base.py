"""Check sisteminin temel tipleri. Bu modül DB'ye veya HTTP'ye bağımlı değildir."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Protocol


class TargetType(StrEnum):
    WEB = "web"
    APK = "apk"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Küçük sayı = daha önemli. Sıralama için."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}


class Category(StrEnum):
    SECURITY = "security"
    PROD_READINESS = "prod_readiness"
    PERFORMANCE = "performance"
    CONVERSION = "conversion"
    STORE_COMPLIANCE = "store_compliance"


@dataclass(frozen=True)
class Finding:
    check_id: str
    severity: Severity
    category: Category
    # Maskeli değer, dosya yolu, header adı vb. ASLA tam secret yok.
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanContext:
    """Collector'ın ürettiği, check'lerin okuduğu bağlam.

    Hedefe özel alanlar alt sınıflarda (WebContext, ApkContext) tanımlanır.
    """

    target_type: TargetType
    target_label: str
    meta: dict[str, Any] = field(default_factory=dict)
    # Check'ler arası paylaşılan hesaplamalar (ör. secret taraması bir kez yapılır)
    cache: dict[str, Any] = field(default_factory=dict)

    async def aclose(self) -> None:
        """Geçici kaynakları (dosyalar, HTTP istemcisi) serbest bırakır. Her durumda çağrılır."""
        return None


class Check(ABC):
    id: ClassVar[str]  # "web.files.env_exposed"
    target: ClassVar[TargetType]
    phase: ClassVar[int] = 1
    category: ClassVar[Category]
    default_severity: ClassVar[Severity]
    # Önemi özel nitelikli veri toplanıp toplanmadığını zaten hesaba katıyorsa True: skorlama
    # bu check'in bulgularını ayrıca yükseltmez (çift sayılmasın).
    considers_special_data: ClassVar[bool] = False
    # Faz 2: check'in verisini üreten derin tarama modülü ("render", "perf", "llm"...). Modül
    # çalışmadıysa check "geçti" listesinde gösterilmez (kontrol edilmemiştir).
    module: ClassVar[str | None] = None

    def finding(
        self, evidence: dict[str, Any] | None = None, severity: Severity | None = None
    ) -> Finding:
        return Finding(
            check_id=self.id,
            severity=severity or self.default_severity,
            category=self.category,
            evidence=evidence or {},
        )

    @abstractmethod
    async def run(self, ctx: ScanContext) -> list[Finding]: ...

    def fingerprint(self, evidence: dict[str, Any]) -> str:
        """Yeniden tarama farkı için kararlı kimlik (PHASE2 §8.10): aynı sorun iki taramada aynı
        parmak izini üretir. Varsayılan: check_id + kanıttaki host/sayfa (çoğu check tek bulgu
        üretir; birden fazla üretenler host/sayfa ile ayrışır)."""
        key = next(
            (str(evidence[k]) for k in ("host", "page", "url", "path") if evidence.get(k)), ""
        )
        return f"{self.id}|{key}"

    def text_params(self, evidence: dict[str, Any], fragment: "FragmentFn") -> dict[str, str]:
        """i18n metinlerindeki `{yer_tutucu}`lar için değerler (rapor üretilirken çağrılır).
        `fragment(ad, **değerler)` check'in i18n `fragments` alanından dile özgü parça üretir.
        Varsayılan: parametre yok (metinler sabit)."""
        return {}


class FragmentFn(Protocol):
    def __call__(self, name: str, **values: object) -> str: ...
