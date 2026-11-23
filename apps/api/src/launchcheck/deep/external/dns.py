"""DNS sorguları (CNAME, A/AAAA, MX, TXT). Sadece okuma: herkese açık DNS kayıtları.

`query` sonucu: `None` → ad yok (NXDOMAIN), `[]` → ad var ama bu tipte kayıt yok, liste → kayıtlar.
Sunucu hatası / zaman aşımı `DnsError` fırlatır (çağıran parçayı "alınamadı" sayar).
"""

from typing import Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver

MAX_CNAME_HOPS = 5


class DnsError(Exception):
    """DNS sunucusu yanıt vermedi veya hata döndü (NXDOMAIN hata değildir)."""


class DnsClient(Protocol):
    async def query(self, name: str, rdtype: str) -> list[str] | None: ...


class SystemDns:
    """dnspython ile sistemin çözücüsü (/etc/resolv.conf). Tembel kurulur."""

    def __init__(self, timeout_s: float) -> None:
        self._timeout_s = timeout_s
        self._resolver: dns.asyncresolver.Resolver | None = None

    def _get(self) -> dns.asyncresolver.Resolver:
        if self._resolver is None:
            self._resolver = dns.asyncresolver.Resolver()
            self._resolver.lifetime = self._timeout_s
        return self._resolver

    async def query(self, name: str, rdtype: str) -> list[str] | None:
        try:
            answer = await self._get().resolve(name, rdtype, raise_on_no_answer=False)
        except dns.resolver.NXDOMAIN:
            return None
        except (dns.exception.DNSException, OSError) as exc:
            raise DnsError(type(exc).__name__) from exc
        if answer.rrset is None:
            return []
        return [_text(rdata, rdtype) for rdata in answer.rrset]


def _text(rdata: object, rdtype: str) -> str:
    if rdtype == "TXT":
        strings = getattr(rdata, "strings", ())
        return b"".join(strings).decode("utf-8", errors="replace")
    if rdtype == "MX":
        return str(getattr(rdata, "exchange", "")).rstrip(".").lower()
    if rdtype == "CNAME":
        return str(getattr(rdata, "target", "")).rstrip(".").lower()
    return str(getattr(rdata, "address", rdata))


async def cname_chain(client: DnsClient, host: str) -> list[str]:
    """host'tan başlayan CNAME zinciri (host hariç). CNAME yoksa boş liste."""
    chain: list[str] = []
    current = host
    for _ in range(MAX_CNAME_HOPS):
        targets = await client.query(current, "CNAME")
        if not targets:
            break
        current = targets[0]
        if current in chain or current == host:
            break  # döngü
        chain.append(current)
    return chain


async def addresses(client: DnsClient, host: str) -> list[str] | None:
    """A + AAAA. `None`: ad hiç yok (NXDOMAIN)."""
    v4 = await client.query(host, "A")
    if v4 is None:
        return None
    v6 = await client.query(host, "AAAA") or []
    return [*v4, *v6]
