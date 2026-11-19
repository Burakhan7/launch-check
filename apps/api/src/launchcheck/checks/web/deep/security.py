"""Tarayıcıda görülen güvenlik sorunları: storage'daki token'lar, çerez bayrakları, sadece
istemcide korunan yönetim sayfaları, source map'ten görünen kaynak ağacı.

Değerler hiçbir yerde yok: storage ve çerezlerin sadece adları ve bayrakları kanıta girer.
"""

import re
from typing import Any

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web.deep._common import MAX_ITEMS, RenderCheck
from launchcheck.deep.context import DeepContext
from launchcheck.deep.render_data import RenderData

TOKEN_KEY_RE = re.compile(r"token|auth|session|jwt|access|refresh|credential", re.IGNORECASE)
LONG_LIVED_DAYS = 30
SESSION_COOKIE_RE = re.compile(
    r"sess|sid\b|^sid|auth|token|jwt|login|remember|connect\.sid|phpsessid|_session",
    re.IGNORECASE,
)
# Ölçüm/pazarlama çerezleri oturum değildir
ANALYTICS_COOKIE_RE = re.compile(r"^(_ga|_gid|_gat|_gcl|_fbp|_fbc|_hj|_clck|_clsk|ajs_|mp_|_uet)")
SOURCEMAP_SAMPLE = 50


@register
class TokenInWebStorage(RenderCheck):
    id = "web.security.token_in_web_storage"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        items: list[dict[str, Any]] = []
        long_lived = False
        for entry in data.storage:
            if not (entry.jwt or TOKEN_KEY_RE.search(entry.key)) or entry.length < 16:
                continue
            item: dict[str, Any] = {"area": entry.area, "key": entry.key, "jwt": entry.jwt}
            if entry.jwt_exp_days is not None:
                item["expires_in_days"] = round(entry.jwt_exp_days, 1)
                long_lived = long_lived or entry.jwt_exp_days > LONG_LIVED_DAYS
            items.append(item)
        if not items:
            return []
        evidence = {"items": items[:MAX_ITEMS], "long_lived": long_lived}
        return [self.finding(evidence, Severity.HIGH if long_lived else Severity.MEDIUM)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"long_lived_note": fragment("long_lived") if evidence.get("long_lived") else ""}


@register
class InsecureCookieFlags(RenderCheck):
    id = "web.security.insecure_cookie_flags"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        https = (data.pages[0].final_url or data.pages[0].url).startswith("https://")
        cookies = []
        for cookie in data.cookies:
            if ANALYTICS_COOKIE_RE.match(cookie.name) or not SESSION_COOKIE_RE.search(cookie.name):
                continue
            issues = []
            if https and not cookie.secure:
                issues.append("missing_secure")
            if not cookie.http_only:
                issues.append("missing_httponly")
            if cookie.same_site.lower() == "none" and not cookie.secure:
                issues.append("samesite_none_insecure")
            if issues:
                cookies.append({"name": cookie.name, "issues": issues})
        if not cookies:
            return []
        return [self.finding({"cookies": cookies[:MAX_ITEMS]})]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        issues = dict.fromkeys(i for c in evidence.get("cookies") or [] for i in c["issues"])
        return {"issues": ", ".join(fragment(i) for i in issues)}


@register
class ClientOnlyAdminRoute(RenderCheck):
    """Sadece sayfa açılır, hiçbir şeye tıklanmaz. Emin değilse info (Faz 3'te doğrulanır)."""

    id = "web.security.client_only_admin_route"
    category = Category.SECURITY
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        confirmed, unsure = [], []
        for probe in data.admin:
            if probe.status not in (200, None) or probe.login_form or probe.redirected_to_login:
                continue
            signals = set(probe.signals)
            item = {"path": probe.path, "signals": sorted(signals)}
            if "admin_heading" in signals and signals & {"data_table", "destructive_buttons"}:
                confirmed.append(item)
            elif signals:
                unsure.append(item)
        if confirmed:
            return [self.finding({"routes": confirmed[:MAX_ITEMS], "verified": True})]
        if unsure:
            evidence = {"routes": unsure[:MAX_ITEMS], "verified": False, "needs_phase3": True}
            return [self.finding(evidence, Severity.INFO)]
        return []


@register
class SourcemapTreeExposed(RenderCheck):
    id = "web.security.sourcemap_tree_exposed"
    category = Category.SECURITY
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        if not data.sourcemap_source_count:
            return []
        evidence = {
            "paths": data.sourcemap_sources[:SOURCEMAP_SAMPLE],
            "path_count": data.sourcemap_source_count,
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"count": str(evidence.get("path_count", 0))}
