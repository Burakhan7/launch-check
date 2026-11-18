"""Herkese açık kalmış dosyalar. SPA catch-all false positive'ine karşı her dosya içerik
doğrulamasından ve rastgele yol (baseline) karşılaştırmasından geçer."""

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.etree.ElementTree import ParseError

import yaml
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import MAX_EVIDENCE_ITEMS, WebCheck, is_html
from launchcheck.collectors.fetcher import FetchResult
from launchcheck.collectors.web_assets import is_same_origin
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import get_settings

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


# --- API dokümantasyonu (Swagger / OpenAPI / ReDoc)

DOC_UI_MARKERS = (b"swagger-ui", b"swaggeruibundle", b"redoc")
SPEC_HEAD_BYTES = 4096
SPEC_URL_RE = re.compile(r"""(?:\burl\s*:\s*|spec-url\s*=\s*)["']([^"'\s]+)["']""")
SENSITIVE_PATH_TOKENS = frozenset(
    {"admin", "internal", "debug", "users", "delete", "export", "upload"}
)
PATH_TOKEN_RE = re.compile(r"[a-z0-9]+")
MAX_SENSITIVE_PATHS = 10
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def _spec_from(res: FetchResult | None) -> tuple[bool, dict[str, Any] | None]:
    """(spec mi?, ayrıştırılabildiyse JSON). Probe limitinde kesilmiş büyük spec'lerde tam JSON
    yerine ilk 4 KB'taki imzaya bakılır (sourcemap check'indeki yaklaşım)."""
    if res is None or is_html(res):
        return False, None
    try:
        data = json.loads(res.body)
    except ValueError:
        data = None
    if isinstance(data, dict):
        is_spec = ("openapi" in data or "swagger" in data) and isinstance(data.get("paths"), dict)
        return is_spec, data if is_spec else None
    head = res.body[:SPEC_HEAD_BYTES]
    signed = (b'"openapi"' in head or b'"swagger"' in head) and b'"paths"' in head
    return bool(res.truncated and signed), None


def _is_doc_ui(res: FetchResult | None) -> bool:
    if res is None or not is_html(res):
        return False
    body = res.body.lower()
    return any(marker in body for marker in DOC_UI_MARKERS)


def _spec_url_in(res: FetchResult, page_url: str, origin: str) -> str | None:
    """Doküman sayfasındaki spec adresi (`url: "/openapi.json"`, `spec-url="..."`)."""
    for match in SPEC_URL_RE.finditer(res.text):
        url = urljoin(page_url, match.group(1))
        if is_same_origin(url, origin):
            parts = urlsplit(url)
            return parts.path + (f"?{parts.query}" if parts.query else "")
    return None


def _spec_summary(spec: dict[str, Any]) -> dict[str, Any]:
    """Sadece yol şablonları ve güvenlik şeması varlığı; örnek değerler, parametre örnekleri ve
    servers adresleri ASLA kanıta girmez."""
    paths: dict[str, Any] = spec.get("paths") or {}
    endpoints = sum(
        len([m for m in ops if str(m).lower() in HTTP_METHODS]) if isinstance(ops, dict) else 0
        for ops in paths.values()
    )
    components = spec.get("components")
    schemes = components.get("securitySchemes") if isinstance(components, dict) else None
    has_schemes = bool(schemes or spec.get("securityDefinitions") or spec.get("security"))
    sensitive = [
        str(p) for p in paths if SENSITIVE_PATH_TOKENS & set(PATH_TOKEN_RE.findall(str(p).lower()))
    ]
    return {
        "endpoint_count": endpoints or len(paths),
        "sensitive_paths": sensitive[:MAX_SENSITIVE_PATHS],
        "has_security_schemes": has_schemes,
    }


@register
class ApiDocsExposed(WebCheck):
    id = "web.files.api_docs_exposed"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        paths = list(get_settings().web_api_doc_paths)
        results = await asyncio.gather(*(ctx.real_file(p) for p in paths))
        spec_path: str | None = None
        spec: dict[str, Any] | None = None
        doc_ui: str | None = None
        doc_res: FetchResult | None = None
        for path, res in zip(paths, results, strict=True):
            is_spec, data = _spec_from(res)
            if is_spec and (spec_path is None or (spec is None and data is not None)):
                spec_path, spec = path, data
            if doc_ui is None and _is_doc_ui(res):
                doc_ui, doc_res = path, res
        if doc_res is not None and doc_ui is not None and spec is None:
            linked = _spec_url_in(doc_res, ctx.origin + doc_ui, ctx.origin)
            if linked and linked not in paths:  # en fazla 1 ek istek
                is_spec, data = _spec_from(await ctx.real_file(linked))
                if is_spec:
                    spec_path, spec = linked, data
        if spec_path is None and doc_ui is None:
            return []
        evidence: dict[str, Any] = {"path": spec_path or doc_ui}
        if doc_ui:
            evidence["doc_ui"] = doc_ui
        severity = Severity.MEDIUM
        if spec is not None:
            summary = _spec_summary(spec)
            evidence.update(summary)
            if not summary["has_security_schemes"] and summary["sensitive_paths"]:
                severity = Severity.HIGH
        return [self.finding(evidence, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        count = evidence.get("endpoint_count")
        return {
            "path": str(evidence.get("path") or ""),
            "endpoints": fragment("endpoints", count=count) if count else "",
        }


# --- Geliştirme dosyaları

NPM_AUTH_RE = re.compile(rb"_authToken|_auth\s*=")
DS_STORE_MAGIC = b"\x00\x00\x00\x01Bud1"
COMPOSE_SERVICES_RE = re.compile(rb"^services\s*:", re.MULTILINE)
MAX_COMPOSE_SERVICES = 10
DevFileRule = Callable[[FetchResult], dict[str, Any] | None]


def _npmrc(res: FetchResult) -> dict[str, Any] | None:
    # Token'ın kendisi asla kanıta girmez; sadece varlığı.
    return {"has_auth_token": True} if NPM_AUTH_RE.search(res.body) else None


def _phpinfo(res: FetchResult) -> dict[str, Any] | None:
    return {} if b"PHP Version" in res.body and b"phpinfo" in res.body else None


def _compose(res: FetchResult) -> dict[str, Any] | None:
    if not COMPOSE_SERVICES_RE.search(res.body):
        return None
    try:
        data = yaml.safe_load(res.body)
    except yaml.YAMLError:
        data = None
    services = data.get("services") if isinstance(data, dict) else None
    # Sadece servis ADLARI; environment ve diğer değerler ASLA yazılmaz.
    names = [str(k) for k in services][:MAX_COMPOSE_SERVICES] if isinstance(services, dict) else []
    return {"services": names}


def _ds_store(res: FetchResult) -> dict[str, Any] | None:
    return {} if res.body.startswith(DS_STORE_MAGIC) else None


def _json_value(res: FetchResult) -> Any:
    try:
        return json.loads(res.body)
    except ValueError:
        return None


def _vscode_settings(res: FetchResult) -> dict[str, Any] | None:
    return {} if isinstance(_json_value(res), dict) else None


def _idea_workspace(res: FetchResult) -> dict[str, Any] | None:
    try:
        SafeET.fromstring(res.body)
    except (ParseError, DefusedXmlException, ValueError):
        return None
    return {}


def _firebaserc(res: FetchResult) -> dict[str, Any] | None:
    data = _json_value(res)
    return {} if isinstance(data, dict) and "projects" in data else None


# yol → (doğrulama, önem, HTML mi bekleniyor)
DEV_FILES: dict[str, tuple[DevFileRule, Severity, bool]] = {
    "/.npmrc": (_npmrc, Severity.CRITICAL, False),
    "/phpinfo.php": (_phpinfo, Severity.HIGH, True),
    "/info.php": (_phpinfo, Severity.HIGH, True),
    "/docker-compose.yml": (_compose, Severity.MEDIUM, False),
    "/docker-compose.yaml": (_compose, Severity.MEDIUM, False),
    "/.DS_Store": (_ds_store, Severity.LOW, False),
    "/.vscode/settings.json": (_vscode_settings, Severity.LOW, False),
    "/.idea/workspace.xml": (_idea_workspace, Severity.LOW, False),
    "/.firebaserc": (_firebaserc, Severity.LOW, False),
}


@register
class DevFilesExposed(WebCheck):
    id = "web.files.dev_files_exposed"
    category = Category.SECURITY
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        paths = list(DEV_FILES)
        results = await asyncio.gather(*(ctx.real_file(p) for p in paths))
        files: list[dict[str, Any]] = []
        severities: list[Severity] = []
        for path, res in zip(paths, results, strict=True):
            rule, severity, expects_html = DEV_FILES[path]
            if res is None or is_html(res) != expects_html:
                continue
            details = rule(res)
            if details is None:
                continue
            files.append({"path": path, **details})
            severities.append(severity)
        if not files:
            return []
        severity = min(severities, key=lambda s: s.rank)
        return [self.finding({"files": files}, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        files: list[dict[str, Any]] = evidence.get("files") or []
        has_npm = any(f.get("has_auth_token") for f in files)
        return {
            "files": ", ".join(str(f.get("path", "")) for f in files),
            "npm_token_step": fragment("npm_token_step") if has_npm else "",
        }
