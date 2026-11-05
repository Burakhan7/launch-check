"""Check sisteminin temel tipleri. Bu modül DB'ye veya HTTP'ye bağımlı değildir."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar


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

    async def aclose(self) -> None:
        """Geçici kaynakları (dosyalar, HTTP istemcisi) serbest bırakır. Her durumda çağrılır."""
        return None


class Check(ABC):
    id: ClassVar[str]  # "web.files.env_exposed"
    target: ClassVar[TargetType]
    phase: ClassVar[int] = 1
    category: ClassVar[Category]
    default_severity: ClassVar[Severity]

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
