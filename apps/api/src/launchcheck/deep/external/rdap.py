"""Alan adı bitiş tarihi (RDAP) ve TLS sertifikası bitiş tarihi.

- RDAP (RFC 9083): rdap.org isteği alan adının kayıt kuruluşunun RDAP sunucusuna yönlendirir.
  Bazı uzantılar (ör. .tr) RDAP sunmaz → veri yok, check atlanır.
- Sertifika: sitenin 443 portuna normal bir TLS bağlantısı (tarayıcının yaptığı gibi), SSRF
  kurallarıyla doğrulanmış IP'ye. Geçersiz sertifika burada değerlendirilmez (Faz 1 HTTPS
  check'leri); doğrulama başarısızsa veri yok sayılır.
"""

import asyncio
import contextlib
import ssl
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from launchcheck.deep.external_data import CertInfo
from launchcheck.security.ssrf import UrlGuard

TLS_PORT = 443


def rdap_url(endpoint: str, domain: str) -> str:
    return endpoint + quote(domain)


def parse_expiry(payload: Any) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    for event in payload.get("events") or []:
        if not isinstance(event, dict) or event.get("eventAction") != "expiration":
            continue
        try:
            value = datetime.fromisoformat(str(event.get("eventDate")).replace("Z", "+00:00"))
        except ValueError:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


def _cert_time(value: str) -> datetime:
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(value), tz=UTC)


def parse_peercert(host: str, cert: dict[str, Any]) -> CertInfo | None:
    try:
        not_after = _cert_time(cert["notAfter"])
        not_before = _cert_time(cert["notBefore"])
    except (KeyError, ValueError):
        return None
    issuer = {k: v for part in cert.get("issuer") or () for k, v in part}
    name = issuer.get("organizationName") or issuer.get("commonName") or ""
    return CertInfo(host=host, not_before=not_before, not_after=not_after, issuer=str(name))


async def fetch_cert(host: str, guard: UrlGuard, timeout_s: float) -> CertInfo | None:
    ips = await guard.resolve_public(host, TLS_PORT)
    context = ssl.create_default_context()
    for ip in ips:
        try:
            async with asyncio.timeout(timeout_s):
                _, writer = await asyncio.open_connection(
                    str(ip), TLS_PORT, ssl=context, server_hostname=host
                )
        except (OSError, TimeoutError, ssl.SSLError):
            continue
        try:
            cert = writer.get_extra_info("peercert")
        finally:
            writer.close()
            with contextlib.suppress(OSError, ssl.SSLError):
                await writer.wait_closed()
        return parse_peercert(host, cert) if isinstance(cert, dict) else None
    return None
