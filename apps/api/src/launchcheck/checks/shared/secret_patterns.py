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

from launchcheck.checks.base import Severity
from launchcheck.checks.shared.masking import mask_secret

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


@dataclass(frozen=True)
class SecretPattern:
    id: str
    regex: re.Pattern[str]
    severity: Severity
    min_entropy: float | None = DEFAULT_MIN_ENTROPY
    # Genel önekli desenlerde (sk-) tireli kelimeler (CSS sınıfları vb.) de yüksek entropi verir;
    # gerçek anahtarlar büyük harf + küçük harf + rakam karışımıdır.
    require_mixed_case_digits: bool = False


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
)
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
    if pattern.min_entropy is None:
        return True
    if pattern.require_mixed_case_digits and not has_mixed_case_and_digits(value):
        return False
    return not is_placeholder(value) and shannon_entropy(value) >= pattern.min_entropy


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
                start, end = m.span()
                if any(s < end and start < e for s, e in taken):
                    continue
                taken.append((start, end))
                value = m.group(0)
                if not _looks_real(pattern, value):
                    continue
                _add(
                    hits,
                    f"{pattern.id}:{value}",
                    lambda p=pattern, v=value: SecretHit(p.id, p.severity, v),
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


def to_evidence(hit: SecretHit) -> dict[str, Any]:
    """Saklanabilir kanıt: maskeli değer + konumlar. Tam değer ASLA burada yok."""
    evidence: dict[str, Any] = {
        "kind": hit.pattern_id,
        "masked": mask_secret(hit.value),
        "locations": [
            {"file": loc.file, "line": loc.line} for loc in hit.locations[:MAX_LOCATIONS]
        ],
        "location_count": len(hit.locations),
    }
    if hit.extra.get("ref"):
        evidence["project_ref"] = hit.extra["ref"]
    return evidence
