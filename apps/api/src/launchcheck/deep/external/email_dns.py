"""E-posta sahteciliği koruması: MX, SPF ve DMARC kayıtları (herkese açık DNS, sadece okuma).

- SPF (RFC 7208): `v=spf1` TXT kaydı; sondaki `all` niteleyicisi (`-all` sıkı, `~all` yumuşak,
  `?all` nötr, `+all` herkese izin). DNS sorgusu gerektiren terimler (include, a, mx, ptr, exists,
  redirect) en fazla 10 olabilir; aşılırsa SPF tümden geçersiz sayılır (permerror).
- DMARC (RFC 7489): `_dmarc.<alan adı>` TXT kaydı, `p=none|quarantine|reject`.
- "Null MX" (RFC 7505, `MX 0 .`): alan adı e-posta almadığını açıkça bildirir.
"""

import re

from launchcheck.deep.external.dns import DnsClient, DnsError
from launchcheck.deep.external_data import EmailDns

SPF_PREFIX_RE = re.compile(r"^v=spf1(\s|$)", re.IGNORECASE)
DMARC_PREFIX_RE = re.compile(r"^v\s*=\s*dmarc1\s*(;|$)", re.IGNORECASE)
ALL_RE = re.compile(r"^([+\-~?]?)all$", re.IGNORECASE)
LOOKUP_TERM_RE = re.compile(r"^[+\-~?]?(include:|exists:|a$|a:|a/|mx$|mx:|mx/|ptr$|ptr:)", re.I)
MAX_SPF_QUERIES = 20  # özyineleme sınırı (kötü niyetli/döngülü kayıtlara karşı)


def spf_records(txt: list[str]) -> list[str]:
    return [t.strip() for t in txt if SPF_PREFIX_RE.match(t.strip())]


def all_qualifier(record: str) -> str | None:
    for term in record.split()[1:]:
        match = ALL_RE.match(term)
        if match:
            return (match.group(1) or "+") + "all"
    return None


def dmarc_policy(record: str) -> str | None:
    for tag in record.split(";"):
        key, _, value = tag.partition("=")
        if key.strip().lower() == "p":
            return value.strip().lower() or None
    return None


class _SpfWalker:
    def __init__(self, dns: DnsClient, max_lookups: int) -> None:
        self._dns = dns
        self._max = max_lookups
        self.lookups = 0
        self._queries = 0

    async def _record(self, domain: str) -> str | None:
        if self._queries >= MAX_SPF_QUERIES:
            return None
        self._queries += 1
        try:
            records = spf_records(await self._dns.query(domain, "TXT") or [])
        except DnsError:
            return None
        return records[0] if len(records) == 1 else None

    async def walk(self, record: str) -> str | None:
        """Sorgu sayısını biriktirir; `all` niteleyicisini (yoksa redirect'inkini) döner."""
        qualifier = all_qualifier(record)
        redirect: str | None = None
        for term in record.split()[1:]:
            if self.lookups > self._max:
                break
            lower = term.lower()
            if lower.startswith("redirect="):
                redirect = term.split("=", 1)[1]
                self.lookups += 1
            elif LOOKUP_TERM_RE.match(lower):
                self.lookups += 1
                if lower.lstrip("+-~?").startswith("include:"):
                    child = await self._record(term.split(":", 1)[1])
                    if child:
                        await self.walk(child)
        if redirect and qualifier is None and self.lookups <= self._max:
            child = await self._record(redirect)
            if child:
                return await self.walk(child)
        return qualifier


async def collect_email(dns: DnsClient, domain: str, max_lookups: int) -> EmailDns:
    """DNS sunucu hatası `DnsError` olarak yukarı çıkar (parça "alınamadı")."""
    mx = await dns.query(domain, "MX") or []
    has_mx = any(m for m in mx)  # "Null MX" → boş exchange
    spf = spf_records(await dns.query(domain, "TXT") or [])
    data = EmailDns(domain=domain, has_mx=has_mx, spf_records=spf)
    if len(spf) == 1:
        walker = _SpfWalker(dns, max_lookups)
        data.spf_all = await walker.walk(spf[0])
        data.spf_lookups = walker.lookups
    dmarc = [
        t.strip()
        for t in await dns.query("_dmarc." + domain, "TXT") or []
        if DMARC_PREFIX_RE.match(t.strip())
    ]
    if len(dmarc) == 1:  # birden fazla DMARC kaydı da geçersizdir (RFC 7489 §6.6.3)
        data.dmarc_present = True
        data.dmarc_policy = dmarc_policy(dmarc[0])
    return data
