"""Dış görünürlük modülünün topladığı veri (PHASE2 §6.3). Ağdan bağımsız: check'ler bunu okur."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class PartStatus:
    OK = "ok"
    SHARED_HOST = "shared_host"  # hedef ortak barındırmada: alan adı platformun
    UNAVAILABLE = "unavailable"  # servis hata verdi / zaman aşımı
    RATE_LIMITED = "rate_limited"  # global hız sınırı (tüm taramalar)
    NO_DATA = "no_data"  # servis yanıt verdi ama bu alan adı için veri yok
    TIMEOUT = "timeout"  # modül bütçesi doldu
    PENDING = "pending"


# Parça → o parçanın verisini okuyan check'ler (parça tamamlanmazsa check atlanır)
PART_CHECKS: dict[str, tuple[str, ...]] = {
    "subdomains": ("web.external.forgotten_subdomain", "web.external.subdomain_takeover_risk"),
    "email": ("web.external.email_spoofing",),
    "wayback": ("web.external.archived_sensitive_paths",),
    "rdap": ("web.external.domain_expiry_soon",),
    "cert": ("web.external.cert_expiry_soon",),
}


@dataclass(frozen=True)
class SubFinding:
    """Alt alan adında çalışan Faz 1 check'inin bulgusu (kanıt zaten maskeli)."""

    check_id: str
    severity: str
    evidence: dict[str, Any]


@dataclass
class SubdomainScan:
    host: str
    url: str  # taranan adres (https varsa https)
    priority: bool  # adı staging/dev/admin... içeriyor
    findings: list[SubFinding] = field(default_factory=list)


@dataclass(frozen=True)
class TakeoverRisk:
    host: str
    cname: str  # servise yönelen CNAME hedefi
    service: str
    reason: str  # "fingerprint" | "nxdomain"
    confidence: str  # "vulnerable" | "edge"


@dataclass
class EmailDns:
    domain: str
    has_mx: bool
    spf_records: list[str] = field(default_factory=list)
    spf_all: str | None = None  # "+all" | "?all" | "~all" | "-all" | None
    spf_lookups: int = 0
    dmarc_policy: str | None = None  # "none" | "quarantine" | "reject" | None (kayıt yok)
    dmarc_present: bool = False


@dataclass(frozen=True)
class ArchivedPath:
    path: str
    kind: str
    archived_at: str  # Wayback zaman damgası (YYYYMMDD)
    live: bool  # şu an hâlâ gerçek içerikle yanıt veriyor


@dataclass(frozen=True)
class DomainExpiry:
    domain: str
    expires_at: datetime


@dataclass(frozen=True)
class CertInfo:
    host: str
    not_before: datetime
    not_after: datetime
    issuer: str


@dataclass
class ExternalData:
    host: str
    domain: str  # kayıtlı alan adı (ör. www.site.com.tr → site.com.tr)
    shared_host: bool
    status: dict[str, str] = field(
        default_factory=lambda: dict.fromkeys(PART_CHECKS, PartStatus.PENDING)
    )
    subdomain_source: str | None = None  # "crtsh" | "certspotter"
    subdomains_found: int = 0  # sertifika kayıtlarındaki tekil ad sayısı
    subdomains_alive: int = 0
    subdomains: list[SubdomainScan] = field(default_factory=list)
    takeovers: list[TakeoverRisk] = field(default_factory=list)
    email: EmailDns | None = None
    archived: list[ArchivedPath] = field(default_factory=list)
    archived_rows: int = 0
    domain_expiry: DomainExpiry | None = None
    cert: CertInfo | None = None

    def ok(self, part: str) -> bool:
        return self.status.get(part) == PartStatus.OK

    def skipped_checks(self) -> list[str]:
        return [c for part, ids in PART_CHECKS.items() if not self.ok(part) for c in ids]
