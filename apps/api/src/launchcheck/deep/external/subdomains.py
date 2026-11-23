"""Alt alan adı keşfi: Sertifika Şeffaflığı kayıtları (crt.sh, yedek: Cert Spotter).

Bir alan adı için verilmiş TLS sertifikaları herkese açık loglara yazılır; crt.sh bu logları
arar (sık sık 502 verir; o zaman SSLMate'in ücretsiz Cert Spotter API'si). Çıkan adlar
tekilleştirilir, wildcard'lar atılır, öncelikli adlar (staging, dev, admin...) öne alınır.
Canlılık ve tarama modülde yapılır.
"""

import re
from typing import Any
from urllib.parse import quote

HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
TOKEN_SPLIT_RE = re.compile(r"[.\-_]")


def crtsh_url(endpoint: str, domain: str) -> str:
    # deduplicate=Y: aynı sertifikanın ön-sertifika kopyaları tek satır (yanıt küçülür)
    return f"{endpoint}?q={quote('%.' + domain)}&output=json&deduplicate=Y"


def certspotter_url(endpoint: str, domain: str) -> str:
    return f"{endpoint}?domain={quote(domain)}&include_subdomains=true&expand=dns_names"


def parse_certspotter(payload: Any, domain: str) -> list[str]:
    """Cert Spotter (SSLMate) yanıtı: [{"dns_names": [...]}, ...]. İlk sayfa yeterli."""
    if not isinstance(payload, list):
        return []
    entries = [
        {"name_value": "\n".join(str(n) for n in e.get("dns_names") or [])}
        for e in payload
        if isinstance(e, dict)
    ]
    return parse_names(entries, domain)


def parse_names(payload: Any, domain: str) -> list[str]:
    """crt.sh JSON'undan alan adına ait tekil host adları (wildcard ve e-postalar hariç)."""
    if not isinstance(payload, list):
        return []
    suffix = "." + domain
    names: set[str] = set()
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        raw = f"{entry.get('name_value') or ''}\n{entry.get('common_name') or ''}"
        for line in raw.split("\n"):
            name = line.strip().lower().rstrip(".")
            if not name or "*" in name or "@" in name:
                continue
            if (name == domain or name.endswith(suffix)) and HOST_RE.match(name):
                names.add(name)
    return sorted(names)


def is_priority(host: str, domain: str, words: list[str]) -> bool:
    label = host[: -len(domain)].rstrip(".") if host.endswith(domain) else host
    tokens = [t for t in TOKEN_SPLIT_RE.split(label) if t]
    return any(t.startswith(w) or t.endswith(w) for t in tokens for w in words)


def twins(host: str) -> set[str]:
    """Hedefin kendisi ve www/yalın ikizi (aynı site sayılır, alt alan adı değildir)."""
    bare = host.removeprefix("www.")
    return {host, bare, "www." + bare}


def select_candidates(
    names: list[str], target_host: str, domain: str, words: list[str], limit: int
) -> list[tuple[str, bool]]:
    """(host, öncelikli mi) listesi: öncelikliler önce, sonra kısa adlar."""
    skip = twins(target_host)
    ranked = sorted(
        ((n, is_priority(n, domain, words)) for n in names if n not in skip),
        key=lambda item: (not item[1], item[0].count("."), len(item[0]), item[0]),
    )
    return ranked[:limit]
