"""Geliştirme/test adresi desenleri (web bundle'ları ve APK string'leri ortak kullanır)."""

import re
from collections.abc import Iterable
from typing import Any

MAX_EVIDENCE_ITEMS = 10
_URL_TAIL = r"[^\s\"'`<>)\\]"

# Port veya yol içermeli: çıplak "http://localhost" kütüphanelerde sık geçer (yanlış alarm).
LOCALHOST_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.0\.2\.2|10\.0\.3\.2)"
    rf"(?::\d{{2,5}}(?:/{_URL_TAIL}*)?|/{_URL_TAIL}+)"
)
# Yerel ağ (LAN) IP'leri: geliştiricinin kendi bilgisayarına işaret eder (mobilde sık görülür)
LAN_URL_RE = re.compile(
    r"https?://(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
    rf"(?::\d{{2,5}})?(?:/{_URL_TAIL}*)?"
)
# Kütüphanelerin varsayılan yerel adresleri (uygulama kodu değil): OpenTelemetry OTLP 4317/4318
LIBRARY_DEFAULT_LOCAL_RE = re.compile(r"^https?://(?:localhost|127\.0\.0\.1):431[78](?:/|$)")
URL_HOST_RE = re.compile(
    rf"https?://([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?::\d+)?(?:/{_URL_TAIL}*)?", re.I
)

NON_PROD_LABELS = {"staging", "stage", "stg", "dev", "develop", "development", "test", "qa", "uat"}
NON_PROD_SUFFIXES = ("-dev", "-staging", "-stg", "-test", "-qa", "-uat")
NON_PROD_TLDS = ("test", "local", "localhost", "internal")
MIN_LABELS_FOR_SUBDOMAIN = 3
# Bilinen, "dev."/"test." ile başlayan ama üretim olan üçüncü taraf alan adları
KNOWN_SAFE_HOSTS = {
    "dev.to",
    "test.com",
    "dev.mysql.com",
    "dev.w3.org",
    "test.example.com",
    "developer.android.com",
}


def is_non_prod_host(host: str) -> bool:
    host = host.lower()
    if host in KNOWN_SAFE_HOSTS:
        return False
    labels = host.split(".")
    first = labels[0]
    # "staging.com" gibi iki parçalı host'lar alan adının kendisidir, alt alan adı değil.
    has_subdomain = len(labels) >= MIN_LABELS_FOR_SUBDOMAIN
    return (
        (has_subdomain and (first in NON_PROD_LABELS or first.endswith(NON_PROD_SUFFIXES)))
        or any(lbl in ("staging", "stg") for lbl in labels[1:-2])
        or labels[-1] in NON_PROD_TLDS
    )


def unique_matches(
    sources: Iterable[tuple[str, str]], *patterns: re.Pattern[str]
) -> dict[str, list[str]]:
    """eşleşen URL → bulunduğu dosyalar (sıra korunur)"""
    found: dict[str, list[str]] = {}
    for name, text in sources:
        for pattern in patterns:
            for m in pattern.finditer(text):
                files = found.setdefault(m.group(0).rstrip(".,;"), [])
                if name not in files:
                    files.append(name)
    return found


def url_evidence(found: dict[str, list[str]]) -> dict[str, Any]:
    urls = list(found)[:MAX_EVIDENCE_ITEMS]
    files = list(dict.fromkeys(f for u in urls for f in found[u]))[:MAX_EVIDENCE_ITEMS]
    return {"urls": urls, "files": files, "count": len(found)}
