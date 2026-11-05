"""Kullanıcının girdiği URL'nin sözdizimsel doğrulaması ve normalizasyonu.

Bu modül DNS çözmez; IP/SSRF kontrolleri `security/ssrf.py` içinde (M2).
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = (80, 443)
DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_URL_LENGTH = 2048


class TargetValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class NormalizedTarget:
    url: str  # origin + path (query ve fragment atılır: kişisel veri/token taşıyabilir)
    origin: str  # https://example.com  (target_label olarak saklanır)
    scheme: str
    host: str
    port: int


def _encode_host(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise TargetValidationError("invalid_url") from exc


def normalize_target_url(raw: str) -> NormalizedTarget:
    text = raw.strip()
    if not text or len(text) > MAX_URL_LENGTH or any(c.isspace() for c in text):
        raise TargetValidationError("invalid_url")
    if "://" not in text:
        text = f"https://{text}"

    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise TargetValidationError("invalid_url") from exc

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise TargetValidationError("url_scheme_not_allowed")
    if parts.username is not None or parts.password is not None or not parts.hostname:
        raise TargetValidationError("invalid_url")
    if port is not None and port not in ALLOWED_PORTS:
        raise TargetValidationError("url_port_not_allowed")

    is_ipv6 = ":" in parts.hostname
    host = parts.hostname.lower() if is_ipv6 else _encode_host(parts.hostname)
    effective_port = port or DEFAULT_PORTS[scheme]
    netloc = f"[{host}]" if is_ipv6 else host
    if effective_port != DEFAULT_PORTS[scheme]:
        netloc = f"{netloc}:{effective_port}"
    origin = f"{scheme}://{netloc}"
    return NormalizedTarget(
        url=origin + (parts.path or "/"),
        origin=origin,
        scheme=scheme,
        host=host,
        port=effective_port,
    )
