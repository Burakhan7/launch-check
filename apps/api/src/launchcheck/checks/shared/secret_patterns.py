"""Web bundle'ları ve APK string'leri için ortak secret desen kütüphanesi (AGENTS.md §9).

Yeni desen eklemek için `PATTERNS` listesine bir `SecretPattern` ekle. Sıra önemlidir:
daha özel desenler (sk-ant-, sk-or-v1-) genel olandan (sk-) önce gelir; çakışan eşleşmelerde
ilk desen kazanır.

Tam secret değerleri sadece bellekte tutulur; dışarıya `to_evidence` ile maskeli çıkar.
"""

import base64
import binascii
import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from launchcheck.checks.base import FragmentFn, Severity
from launchcheck.checks.shared.domains import registered_domain
from launchcheck.checks.shared.masking import MASK_FILLER, mask_secret

# Token'ın önünde/arkasında harf, rakam, _ veya - olmamalı (daha uzun bir kelimenin parçası değil)
_B = r"(?<![A-Za-z0-9_\-])"
_E = r"(?![A-Za-z0-9_\-])"
DEFAULT_MIN_ENTROPY = 3.0
MAX_LOCATIONS = 10
PLACEHOLDER_MARKERS = (
    "xxxx",
    "your",
    "example",
    "placeholder",
    "dummy",
    "sample",
    "changeme",
    "replace",
    "insert",
    "redacted",
    "1234567890",
    "abcdefghij",
    "*****",
    "....",
)
_REPEATED_RUN = re.compile(r"(.)\1{7,}")


# Bağlamlı desenlerde bağlam regex'i eşleşmenin en fazla bu kadar karakter yakınında aranır
CONTEXT_WINDOW_CHARS = 2000


@dataclass(frozen=True)
class SecretPattern:
    id: str
    regex: re.Pattern[str]
    severity: Severity
    min_entropy: float | None = DEFAULT_MIN_ENTROPY
    # Genel önekli desenlerde (sk-) tireli kelimeler (CSS sınıfları vb.) de yüksek entropi verir;
    # gerçek anahtarlar büyük harf + küçük harf + rakam karışımıdır.
    require_mixed_case_digits: bool = False
    # Öneksiz anahtarlar (iyzico, PayTR): eşleşme ancak aynı kaynakta, yakında bu regex de
    # geçiyorsa sayılır. Değer `value` adlı yakalama grubundan okunur.
    context: re.Pattern[str] | None = None
    # (önek, önem): değer bu önekle başlıyorsa önem düşer (ör. iyzico "sandbox-" → medium)
    low_risk_prefix: tuple[str, Severity] | None = None

    def value_span(self, m: re.Match[str]) -> tuple[int, int]:
        return m.span("value") if "value" in self.regex.groupindex else m.span()

    def severity_for(self, value: str) -> Severity:
        if self.low_risk_prefix and value.startswith(self.low_risk_prefix[0]):
            return self.low_risk_prefix[1]
        return self.severity


def _p(
    pattern_id: str,
    body: str,
    severity: Severity,
    min_entropy: float | None = DEFAULT_MIN_ENTROPY,
    require_mixed_case_digits: bool = False,
) -> SecretPattern:
    return SecretPattern(
        pattern_id, re.compile(_B + body + _E), severity, min_entropy, require_mixed_case_digits
    )


def _ctx(
    pattern_id: str,
    body: str,
    context: str,
    severity: Severity,
    low_risk_prefix: tuple[str, Severity] | None = None,
) -> SecretPattern:
    return SecretPattern(
        pattern_id,
        re.compile(body),
        severity,
        context=re.compile(context),
        low_risk_prefix=low_risk_prefix,
    )


PATTERNS: tuple[SecretPattern, ...] = (
    _p("anthropic", r"sk-ant-[a-z0-9]+-[A-Za-z0-9_\-]{80,}", Severity.CRITICAL),
    _p("openrouter", r"sk-or-v1-[a-f0-9]{64}", Severity.CRITICAL),
    _p(
        "openai",
        r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}",
        Severity.CRITICAL,
        require_mixed_case_digits=True,
    ),
    _p("groq", r"gsk_[A-Za-z0-9]{40,}", Severity.CRITICAL),
    _p("npm_token", r"npm_[A-Za-z0-9]{36}", Severity.CRITICAL),
    _p("huggingface", r"hf_[A-Za-z0-9]{34,}", Severity.HIGH),
    # Sadece secret token (sk.); public token (pk.) bilinçli olarak dışarıda
    _p("mapbox_secret", r"sk\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", Severity.HIGH),
    _p(
        "slack_webhook",
        r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]{20,}",
        Severity.HIGH,
        min_entropy=None,
    ),
    _p("slack_token", r"xox[baprs]-[0-9A-Za-z\-]{10,}", Severity.HIGH),
    _p(
        "discord_webhook",
        r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d{17,20}/[A-Za-z0-9_\-]{60,}",
        Severity.HIGH,
        min_entropy=None,
    ),
    _p("telegram_bot_token", r"\d{8,10}:AA[A-Za-z0-9_\-]{33}", Severity.HIGH),
    _p("mailgun", r"key-[0-9a-f]{32}", Severity.HIGH),
    _p(
        "cloudinary_url",
        r"cloudinary://\d{12,16}:[A-Za-z0-9_\-]{20,}@[a-z0-9_\-]+",
        Severity.HIGH,
        min_entropy=None,
    ),
    _p("stripe_live_secret", r"(?:sk|rk)_live_[0-9a-zA-Z]{20,}", Severity.CRITICAL),
    _p("stripe_test_secret", r"sk_test_[0-9a-zA-Z]{20,}", Severity.MEDIUM),
    _p("supabase_secret", r"sb_secret_[A-Za-z0-9_\-]{20,}", Severity.CRITICAL),
    _p("aws_access_key", r"AKIA[0-9A-Z]{16}", Severity.CRITICAL),
    _p("github_token", r"gh[pousr]_[A-Za-z0-9]{36,}", Severity.CRITICAL),
    _p("sendgrid", r"SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}", Severity.HIGH),
    _p("resend", r"re_[A-Za-z0-9]{8,}_[A-Za-z0-9]{16,}", Severity.HIGH),
    _p("twilio", r"SK[0-9a-fA-F]{32}", Severity.HIGH),
    _p("replicate", r"r8_[A-Za-z0-9]{30,}", Severity.HIGH),
    SecretPattern(
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
        Severity.CRITICAL,
        min_entropy=None,
    ),
    _p("google_api_key", r"AIza[0-9A-Za-z_\-]{35}", Severity.MEDIUM),
    # Öneksiz ödeme anahtarları (TR pazarı): sadece sağlayıcı adı yakında geçiyorsa
    _ctx(
        "iyzico_secret",
        r"(?i)(?:secret_?key|iyzi(?:co|pay)_?secret(?:_?key)?)[\"'\s]*[:=]\s*[\"']"
        r"(?P<value>(?:sandbox-)?[A-Za-z0-9]{20,})[\"']",
        r"(?i)iyzi(?:co|pay)",
        Severity.CRITICAL,
        low_risk_prefix=("sandbox-", Severity.MEDIUM),
    ),
    _ctx(
        "paytr_merchant_secret",
        r"(?i)(?:merchant_?(?:key|salt)|paytr_?merchant_?(?:key|salt))[\"'\s]*[:=]\s*[\"']"
        r"(?P<value>[A-Za-z0-9]{12,})[\"']",
        r"(?i)paytr",
        Severity.CRITICAL,
    ),
)
# Ödeme sağlayıcısı anahtarları: düzeltme metnine ödeme özelinde bir adım eklenir
PAYMENT_PATTERNS = frozenset({"iyzico_secret", "paytr_merchant_secret"})
# Google anahtarları ayrı bir check'te raporlanır (Firebase config'i normalde public'tir).
GOOGLE_API_KEY_PATTERN = "google_api_key"

SUPABASE_JWT_RE = re.compile(_B + r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+" + _E)


@dataclass(frozen=True)
class Location:
    file: str
    line: int


@dataclass
class SecretHit:
    pattern_id: str
    severity: Severity
    value: str  # sadece bellekte; asla saklanmaz/loglanmaz
    locations: list[Location] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    n = len(text)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def is_placeholder(value: str) -> bool:
    lower = value.lower()
    return any(m in lower for m in PLACEHOLDER_MARKERS) or bool(_REPEATED_RUN.search(value))


def has_mixed_case_and_digits(value: str) -> bool:
    return (
        any(c.isupper() for c in value)
        and any(c.islower() for c in value)
        and any(c.isdigit() for c in value)
    )


def _looks_real(pattern: SecretPattern, value: str) -> bool:
    # Yer tutucu kontrolü entropisiz desenlerde de yapılır (Slack dokümanlarındaki
    # .../T00000000/B00000000/XXXXXXXX... örneği gibi).
    if is_placeholder(value):
        return False
    if pattern.min_entropy is None:
        return True
    if pattern.require_mixed_case_digits and not has_mixed_case_and_digits(value):
        return False
    return shannon_entropy(value) >= pattern.min_entropy


def _has_context(pattern: SecretPattern, text: str, start: int, end: int) -> bool:
    if pattern.context is None:
        return True
    window = text[max(0, start - CONTEXT_WINDOW_CHARS) : end + CONTEXT_WINDOW_CHARS]
    return bool(pattern.context.search(window))


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _add(hits: dict[str, SecretHit], hit_key: str, make: Any, location: Location) -> None:
    hit = hits.get(hit_key)
    if hit is None:
        hit = make()
        hits[hit_key] = hit
    if location not in hit.locations:
        hit.locations.append(location)


def find_secrets(sources: Iterable[tuple[str, str]]) -> list[SecretHit]:
    """`sources`: (konum adı, içerik). Aynı secret birden fazla yerde çıkarsa tek hit döner."""
    hits: dict[str, SecretHit] = {}
    for name, text in sources:
        taken: list[tuple[int, int]] = []
        for pattern in PATTERNS:
            for m in pattern.regex.finditer(text):
                start, end = pattern.value_span(m)
                if any(s < end and start < e for s, e in taken):
                    continue
                value = text[start:end]
                if not _has_context(pattern, text, start, end):
                    continue
                taken.append((start, end))
                if not _looks_real(pattern, value):
                    continue
                _add(
                    hits,
                    f"{pattern.id}:{value}",
                    lambda p=pattern, v=value: SecretHit(p.id, p.severity_for(v), v),
                    Location(name, _line_of(text, start)),
                )
    return list(hits.values())


def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def find_supabase_jwts(sources: Iterable[tuple[str, str]]) -> list[SecretHit]:
    """Supabase JWT'leri. `extra.role` service_role/anon; `extra.ref` proje kimliği."""
    hits: dict[str, SecretHit] = {}
    for name, text in sources:
        for m in SUPABASE_JWT_RE.finditer(text):
            token = m.group(0)
            payload = decode_jwt_payload(token)
            issuer = str(payload.get("iss", "supabase")) if payload else ""
            if not payload or not issuer.startswith("supabase") or "role" not in payload:
                continue
            role = str(payload.get("role"))
            _add(
                hits,
                token,
                lambda t=token, r=role, p=payload: SecretHit(
                    "supabase_jwt",
                    Severity.CRITICAL if r == "service_role" else Severity.INFO,
                    t,
                    extra={"role": r, "ref": str(p.get("ref", ""))},
                ),
                Location(name, _line_of(text, m.start())),
            )
    return list(hits.values())


CLOUDINARY_URL_RE = re.compile(r"^cloudinary://(\d+):[^@]+@(.+)$")


def masked_value(hit: SecretHit) -> str:
    """Bağlantı adresi biçimli secret'larda şifre kısmı hiçbir biçimde gösterilmez."""
    m = CLOUDINARY_URL_RE.match(hit.value) if hit.pattern_id == "cloudinary_url" else None
    if m:
        return f"cloudinary://{mask_secret(m.group(1))}:{MASK_FILLER}@{m.group(2)}"
    return mask_secret(hit.value)


def to_evidence(hit: SecretHit) -> dict[str, Any]:
    """Saklanabilir kanıt: maskeli değer + konumlar. Tam değer ASLA burada yok."""
    evidence: dict[str, Any] = {
        "kind": hit.pattern_id,
        "masked": masked_value(hit),
        "locations": [
            {"file": loc.file, "line": loc.line} for loc in hit.locations[:MAX_LOCATIONS]
        ],
        "location_count": len(hit.locations),
    }
    if hit.extra.get("ref"):
        evidence["project_ref"] = hit.extra["ref"]
    return evidence


# --- Veritabanı bağlantı adresleri (kullanıcı + şifre + host birlikte)

CONNECTION_STRING_RE = re.compile(
    r"(?<![A-Za-z0-9+.\-])"
    r"(?P<scheme>postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?)://"
    r"(?P<user>[^:@/\s\"'`]+):(?P<password>[^@\s\"'`]+)@(?P<host>[^/\s\"'`:?,]+)"
)
SCHEME_KINDS = {
    "postgresql": "postgres",
    "mongodb+srv": "mongodb",
    "rediss": "redis",
    "amqps": "amqp",
}
PLACEHOLDER_PASSWORDS = frozenset({"password", "pass", "pwd", "secret", "changeme"})
PLACEHOLDER_PASSWORD_PREFIXES = ("$", "{", "<", "[", "%s", "*")
# Şifre bundan kısaysa (u:p, user:pass) örnek değer sayılır
MIN_REAL_PASSWORD_LEN = 6
LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"})  # noqa: S104
LOCAL_DB_SUFFIXES = (".local", ".internal", ".test", ".localhost", ".invalid", ".example")
EXAMPLE_DB_DOMAINS = frozenset({"example.com", "example.org", "example.net"})
# Yönetilen DB servisleri (kayıtlı alan adı): bağlantı doğrudan internetten açık → critical
MANAGED_DB_DOMAINS = frozenset(
    {
        "supabase.co",
        "supabase.com",
        "neon.tech",
        "planetscale.com",
        "mongodb.net",
        "upstash.io",
        "railway.app",
        "rlwy.net",
        "render.com",
        "aivencloud.com",
        "cockroachlabs.cloud",
    }
)
MANAGED_DB_SUFFIXES = {".rds.amazonaws.com": "rds.amazonaws.com"}


@dataclass
class ConnectionHit:
    """Bulunan bağlantı adresi. Kullanıcı adı, şifre ve tam host SADECE bellekteki `key`
    içinde (tekilleştirme için) durur; kanıta hiçbiri yazılmaz."""

    key: str
    kind: str
    provider_domain: str
    password_length: int
    severity: Severity
    locations: list[Location] = field(default_factory=list)


@dataclass
class ConnectionScan:
    hits: list[ConnectionHit]
    local_count: int  # localhost/docker gibi yerel adresler: bulgu değil, meta sayacı


def _is_placeholder_password(user: str, password: str) -> bool:
    lower = password.lower()
    return (
        lower in PLACEHOLDER_PASSWORDS
        or is_placeholder(password)
        or password.startswith(PLACEHOLDER_PASSWORD_PREFIXES)
        or len(password) < MIN_REAL_PASSWORD_LEN
        or user == password
    )


def _is_local_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return (
        host in LOCAL_DB_HOSTS
        or "." not in host  # tek parçalı: db, postgres, redis, mongo (docker servis adı)
        or host.endswith(LOCAL_DB_SUFFIXES)
        or registered_domain(host) in EXAMPLE_DB_DOMAINS
    )


def _provider(host: str) -> tuple[str, bool]:
    """(kayıtlı alan adı, yönetilen DB servisi mi?)"""
    host = host.lower().rstrip(".")
    for suffix, name in MANAGED_DB_SUFFIXES.items():
        if host.endswith(suffix):
            return name, True
    domain = registered_domain(host)
    return domain, domain in MANAGED_DB_DOMAINS


def find_connection_strings(sources: Iterable[tuple[str, str]]) -> ConnectionScan:
    """Kullanıcı adı + şifre içeren DB bağlantı adresleri. Yer tutucu şifreler ve örnek değerler
    elenir; yerel host'lar sayılır ama bulgu olmaz."""
    hits: dict[str, ConnectionHit] = {}
    local: set[str] = set()
    for name, text in sources:
        for m in CONNECTION_STRING_RE.finditer(text):
            user, password, host = m.group("user"), m.group("password"), m.group("host")
            if _is_placeholder_password(user, password):
                continue
            key = m.group(0)
            if _is_local_host(host):
                local.add(key)
                continue
            scheme = m.group("scheme").lower()
            domain, managed = _provider(host)
            hit = hits.get(key)
            if hit is None:
                hit = ConnectionHit(
                    key=key,
                    kind=SCHEME_KINDS.get(scheme, scheme),
                    provider_domain=domain,
                    password_length=len(password),
                    severity=Severity.CRITICAL if managed else Severity.HIGH,
                )
                hits[key] = hit
            location = Location(name, _line_of(text, m.start()))
            if location not in hit.locations:
                hit.locations.append(location)
    return ConnectionScan(list(hits.values()), len(local))


def connection_findings_meta(scan: ConnectionScan) -> dict[str, Any]:
    """Yerel (localhost/docker) bağlantı adresleri bulgu değildir; sadece sayıları meta'ya."""
    return {"local_db_strings": scan.local_count} if scan.local_count else {}


def leaked_key_text_params(evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
    """Ödeme sağlayıcısı anahtarlarında (iyzico, PayTR) düzeltme metnine ödeme adımı eklenir."""
    payment = evidence.get("kind") in PAYMENT_PATTERNS
    return {"payment_step": fragment("payment_step") if payment else ""}


def connection_evidence(hit: ConnectionHit) -> dict[str, Any]:
    """Saklanabilir kanıt. Şifre, kullanıcı adı ve tam host ASLA (maskeli hâli de) yok."""
    return {
        "kind": hit.kind,
        "provider_domain": hit.provider_domain,
        "password_length": hit.password_length,
        "locations": [
            {"file": loc.file, "line": loc.line} for loc in hit.locations[:MAX_LOCATIONS]
        ],
        "location_count": len(hit.locations),
    }
