"""Internet Archive (Wayback Machine) CDX: sitenin geçmişte arşivlenmiş adreslerinde hassas yollar.

Arşivde `.env`, `.git/config`, veritabanı yedeği gibi bir yol varsa bu dosya bir dönem herkese
açıktı. Eşleşen yollardan en fazla N tanesi şimdi tekrar istenir (normal GET, sorgu dizesi
olmadan: arşivdeki adreslerde token olabilir, kullanılmaz) ve Faz 1'in içerik doğrulamasından
geçer. Kanıta sadece yol yazılır, dosya içeriği asla.
"""

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from launchcheck.checks.web._common import is_html
from launchcheck.checks.web.files import (
    ENV_LINE_RE,
    GIT_HEAD_RE,
    SENSITIVE_JSON_KEY_RE,
    ZIP_MAGIC,
    _is_doc_ui,
    _is_sensitive_json,
    _is_sql_dump,
    _phpinfo,
    _spec_from,
)
from launchcheck.collectors.fetcher import FetchResult
from launchcheck.collectors.web_collector import WebContext
from launchcheck.deep.external_data import ArchivedPath

# Önem sırası (kanıt ve deneme sırası)
KIND_ORDER = ("env", "git", "sql", "backup", "config", "phpinfo", "api_docs")
ARCHIVE_MAGICS = (ZIP_MAGIC, b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07")
Validator = Callable[[str, FetchResult], bool]


def cdx_url(endpoint: str, host: str, max_rows: int) -> str:
    return (
        f"{endpoint}?url={quote(host)}/*&output=json&fl=original,timestamp"
        f"&filter=statuscode:200&collapse=urlkey&limit={max_rows}"
    )


def parse_rows(payload: Any) -> list[tuple[str, str]]:
    if not isinstance(payload, list):
        return []
    rows = []
    for row in payload:
        if isinstance(row, list) and len(row) >= 2 and row[0] != "original":
            rows.append((str(row[0]), str(row[1])))
    return rows


def sensitive_paths(
    rows: list[tuple[str, str]], host: str, patterns: dict[str, str]
) -> list[tuple[str, str, str]]:
    """(istek yolu, tür, arşiv tarihi YYYYMMDD); yol başına en yeni kayıt, önem sırasında."""
    compiled = [(k, re.compile(rx, re.IGNORECASE)) for k, rx in patterns.items()]
    found: dict[str, tuple[str, str]] = {}
    for original, timestamp in rows:
        parts = urlsplit(original if "://" in original else "http://" + original)
        if (parts.hostname or "").lower() not in (host, "www." + host, host.removeprefix("www.")):
            continue
        path = parts.path or "/"
        readable = unquote(path).lower()
        for kind, rx in compiled:
            if rx.search(readable):
                previous = found.get(path)
                if previous is None or timestamp > previous[1]:
                    found[path] = (kind, timestamp)
                break
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    items = [(p, k, ts[:8]) for p, (k, ts) in found.items()]
    return sorted(items, key=lambda i: (order.get(i[1], len(order)), i[0]))


def _not_html(res: FetchResult) -> bool:
    return not is_html(res)


def _env(path: str, res: FetchResult) -> bool:
    return _not_html(res) and bool(ENV_LINE_RE.search(res.text))


def _git(path: str, res: FetchResult) -> bool:
    if path.lower().endswith("head"):
        return bool(GIT_HEAD_RE.match(res.body.lstrip()))
    return b"[core]" in res.body


def _sql(path: str, res: FetchResult) -> bool:
    return _not_html(res) and (_is_sql_dump(res) or res.body.startswith(b"\x1f\x8b"))


def _backup(path: str, res: FetchResult) -> bool:
    if not _not_html(res):
        return False
    return res.body.startswith(ARCHIVE_MAGICS) or path.lower().endswith(".bak")


def _config(path: str, res: FetchResult) -> bool:
    if not _not_html(res):
        return False
    return _is_sensitive_json(res) or bool(SENSITIVE_JSON_KEY_RE.search(res.text[:65536]))


def _phpinfo_page(path: str, res: FetchResult) -> bool:
    return _phpinfo(res) is not None


def _api_docs(path: str, res: FetchResult) -> bool:
    is_spec, _ = _spec_from(res)
    return is_spec or _is_doc_ui(res)


VALIDATORS: dict[str, Validator] = {
    "env": _env,
    "git": _git,
    "sql": _sql,
    "backup": _backup,
    "config": _config,
    "phpinfo": _phpinfo_page,
    "api_docs": _api_docs,
}


async def probe_paths(
    prober: WebContext, items: list[tuple[str, str, str]], max_probes: int
) -> list[ArchivedPath]:
    """İlk `max_probes` yolu şimdi dener (SPA catch-all elenir). Denenmeyenler listeye girmez:
    şu anki durumlarını bilmediğimiz için "artık yok" diyemeyiz."""
    out = []
    for path, kind, archived_at in items[:max_probes]:
        res = await prober.real_file(path)
        validator = VALIDATORS.get(kind)
        live = res is not None and validator is not None and validator(path, res)
        out.append(ArchivedPath(path, kind, archived_at, live))
    return out
