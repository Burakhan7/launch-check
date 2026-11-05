"""Herkese açık kalmış dosyalar. SPA catch-all false positive'ine karşı her dosya içerik
doğrulamasından ve rastgele yol (baseline) karşılaştırmasından geçer."""

import asyncio
import json
import re
from urllib.parse import urljoin

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import MAX_EVIDENCE_ITEMS, WebCheck, is_html
from launchcheck.collectors.fetcher import FetchResult
from launchcheck.collectors.web_assets import is_same_origin
from launchcheck.collectors.web_collector import WebContext

ENV_PATHS = ("/.env", "/.env.local", "/.env.production", "/.env.development")
ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{1,63})\s*=", re.MULTILINE)
GIT_HEAD_RE = re.compile(rb"^(ref: refs/|[0-9a-f]{40}\s*$)")
SQL_MARKERS = (b"create table", b"insert into", b"-- mysql dump", b"postgresql database dump")
ZIP_MAGIC = b"PK\x03\x04"
SENSITIVE_JSON_KEY_RE = re.compile(
    r"secret|password|passwd|token|api[_-]?key|private|credential|database|db_|dsn", re.IGNORECASE
)
SOURCEMAP_URL_RE = re.compile(r"[#@]\s*sourceMappingURL\s*=\s*(\S+)\s*$", re.MULTILINE)
MAX_SOURCEMAP_PROBES = 5
SOURCEMAP_SIGNATURE_RE = re.compile(rb'^\s*\{\s*"(version|file|sources|mappings|names)"\s*:')


@register
class EnvExposed(WebCheck):
    id = "web.files.env_exposed"
    category = Category.SECURITY
    default_severity = Severity.CRITICAL

    async def check(self, ctx: WebContext) -> list[Finding]:
        results = await asyncio.gather(*(ctx.real_file(p) for p in ENV_PATHS))
        findings = []
        for path, res in zip(ENV_PATHS, results, strict=True):
            if res is None or is_html(res):
                continue
            # Sadece değişken ADLARI kanıt olarak tutulur; değerler asla.
            names = list(dict.fromkeys(ENV_LINE_RE.findall(res.text)))
            if names:
                findings.append(self.finding({"path": path, "keys": names[:MAX_EVIDENCE_ITEMS]}))
        return findings


@register
class GitExposed(WebCheck):
    id = "web.files.git_exposed"
    category = Category.SECURITY
    default_severity = Severity.CRITICAL

    async def check(self, ctx: WebContext) -> list[Finding]:
        head, config = await asyncio.gather(
            ctx.real_file("/.git/HEAD"), ctx.real_file("/.git/config")
        )
        paths = []
        if head is not None and GIT_HEAD_RE.match(head.body.lstrip()):
            paths.append("/.git/HEAD")
        if config is not None and b"[core]" in config.body:
            paths.append("/.git/config")
        return [self.finding({"paths": paths})] if paths else []


def _is_sql_dump(res: FetchResult) -> bool:
    head = res.body[:65536].lower()
    return any(m in head for m in SQL_MARKERS)


def _is_sensitive_json(res: FetchResult) -> bool:
    try:
        data = json.loads(res.body)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    return any(SENSITIVE_JSON_KEY_RE.search(k) for k in _all_keys(data))


def _all_keys(data: object, depth: int = 0) -> list[str]:
    if depth > 4 or not isinstance(data, dict):
        return []
    keys = [str(k) for k in data]
    for v in data.values():
        keys += _all_keys(v, depth + 1)
    return keys


BACKUP_FILES = {
    "/backup.sql": _is_sql_dump,
    "/db.sql": _is_sql_dump,
    "/dump.sql": _is_sql_dump,
    "/backup.zip": lambda r: r.body.startswith(ZIP_MAGIC),
    "/config.json": _is_sensitive_json,
}


@register
class BackupExposed(WebCheck):
    id = "web.files.backup_exposed"
    category = Category.SECURITY
    default_severity = Severity.HIGH

    async def check(self, ctx: WebContext) -> list[Finding]:
        paths = list(BACKUP_FILES)
        results = await asyncio.gather(*(ctx.real_file(p) for p in paths))
        return [
            self.finding({"path": path})
            for path, res in zip(paths, results, strict=True)
            if res is not None and not is_html(res) and BACKUP_FILES[path](res)
        ]


def _sourcemap_candidates(ctx: WebContext) -> list[str]:
    candidates = []
    for script in ctx.scripts:
        if script.inline:
            continue
        matches = list(SOURCEMAP_URL_RE.finditer(script.content[-2000:]))
        match = matches[-1] if matches else None
        if match and not match.group(1).startswith("data:"):
            url = urljoin(script.url, match.group(1))
        else:
            url = script.url.split("?", 1)[0] + ".map"
        if is_same_origin(url, ctx.origin):
            candidates.append(url)
    return list(dict.fromkeys(candidates))[:MAX_SOURCEMAP_PROBES]


def _is_sourcemap(res: FetchResult | None) -> bool:
    """Map dosyaları genelde probe limitinden (1 MB) büyüktür ve kesilir; bu yüzden tam JSON
    ayrıştırmak yerine başlangıç imzasına bakılır."""
    if res is None or res.status != 200 or is_html(res):
        return False
    return bool(SOURCEMAP_SIGNATURE_RE.match(res.body[:4096])) and (
        b'"mappings"' in res.body or b'"sources"' in res.body
    )


@register
class SourcemapExposed(WebCheck):
    id = "web.files.sourcemap_exposed"
    category = Category.SECURITY
    default_severity = Severity.HIGH

    async def check(self, ctx: WebContext) -> list[Finding]:
        urls = _sourcemap_candidates(ctx)
        results = await asyncio.gather(*(ctx.fetch_url(u) for u in urls))
        exposed = [u for u, r in zip(urls, results, strict=True) if _is_sourcemap(r)]
        return [self.finding({"files": exposed})] if exposed else []
