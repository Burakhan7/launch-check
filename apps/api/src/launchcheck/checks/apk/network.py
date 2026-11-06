"""Ağ ayarları ve gömülü adresler."""

import re
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET

from launchcheck.checks.apk._common import ApkCheck
from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.dev_urls import (
    LAN_URL_RE,
    LIBRARY_DEFAULT_LOCAL_RE,
    LOCALHOST_URL_RE,
    MAX_EVIDENCE_ITEMS,
    URL_HOST_RE,
    is_non_prod_host,
    unique_matches,
    url_evidence,
)
from launchcheck.collectors.apk_collector import ApkContext, android_attr
from launchcheck.collectors.apk_framework import Framework

# RN/Expo'nun kendi geliştirme araçlarının portları (Metro, React DevTools, Expo CLI).
# RN release build'lerinde de framework kodu içinde bulunurlar; rn_dev_mode check'i ayrıca bakar.
RN_TOOLING_PORT_RE = re.compile(r"^https?://[^/]+:(8081|8097|19000|19001|19002)(?:/|$)")
# Sadece API'ye benzeyen http:// adresleri: kütüphanelerde XML namespace vb. http:// çok yaygın.
API_LIKE_RE = re.compile(r"^http://(?:api[.-][^/]+|[^/]+/(?:api|v\d+|graphql|rest)(?:/|$))", re.I)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "10.0.2.2", "10.0.3.2"}
CLEARTEXT_DEFAULT_UNTIL_SDK = 28  # targetSdk < 28: cleartext varsayılan olarak açık


def _is_rn_tooling(ctx: ApkContext, url: str) -> bool:
    return ctx.framework in (Framework.REACT_NATIVE, Framework.EXPO) and bool(
        RN_TOOLING_PORT_RE.match(url)
    )


@register
class LocalhostUrls(ApkCheck):
    id = "apk.network.localhost_urls"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: ApkContext) -> list[Finding]:
        found = {
            url: files
            for url, files in unique_matches(
                ctx.string_sources, LOCALHOST_URL_RE, LAN_URL_RE
            ).items()
            if not LIBRARY_DEFAULT_LOCAL_RE.match(url) and not _is_rn_tooling(ctx, url)
        }
        return [self.finding(url_evidence(found))] if found else []


@register
class StagingUrls(ApkCheck):
    id = "apk.network.staging_urls"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: ApkContext) -> list[Finding]:
        found = {
            url: files
            for url, files in unique_matches(ctx.string_sources, URL_HOST_RE).items()
            if is_non_prod_host(urlsplit(url).hostname or "")
        }
        return [self.finding(url_evidence(found))] if found else []


def _network_config(ctx: ApkContext) -> Element | None:
    ref = (
        android_attr(ctx.application, "networkSecurityConfig")
        if ctx.application is not None
        else None
    )
    if not ref or not ref.startswith("@xml/"):
        return None
    path: Path = ctx.decoded_dir / "res" / "xml" / f"{ref.removeprefix('@xml/')}.xml"
    try:
        return SafeET.parse(path).getroot()
    except Exception:
        return None


def _permits_cleartext(node: Element | None) -> bool:
    return node is not None and (node.get("cleartextTrafficPermitted") or "").lower() == "true"


def _cleartext_domains(config: Element) -> list[str]:
    domains = []
    for domain_config in config.iter("domain-config"):
        if _permits_cleartext(domain_config):
            names = [(d.text or "").strip() for d in domain_config.findall("domain")]
            domains += [n for n in names if n and n not in LOCAL_HOSTS]
    return list(dict.fromkeys(domains))


@register
class Cleartext(ApkCheck):
    id = "apk.network.cleartext"
    category = Category.SECURITY
    default_severity = Severity.HIGH

    async def check(self, ctx: ApkContext) -> list[Finding]:
        app = ctx.application
        reasons: list[str] = []
        evidence: dict[str, object] = {}
        attr = (android_attr(app, "usesCleartextTraffic") or "").lower() if app is not None else ""
        config = _network_config(ctx)
        if attr == "true":
            reasons.append("manifest_uses_cleartext_traffic")
        elif not attr and config is None and (ctx.target_sdk or 0) < CLEARTEXT_DEFAULT_UNTIL_SDK:
            reasons.append("default_for_old_target_sdk")
        if config is not None:
            if _permits_cleartext(config.find("base-config")):
                reasons.append("network_security_config_base")
            domains = _cleartext_domains(config)
            if domains:
                reasons.append("network_security_config_domains")
                evidence["domains"] = domains[:MAX_EVIDENCE_ITEMS]
        http_apis = [
            u
            for u in unique_matches(ctx.string_sources, URL_HOST_RE)
            if API_LIKE_RE.match(u) and (urlsplit(u).hostname or "") not in LOCAL_HOSTS
        ]
        if http_apis:
            reasons.append("http_api_urls")
            evidence["urls"] = http_apis[:MAX_EVIDENCE_ITEMS]
        return [self.finding({"reasons": reasons, **evidence})] if reasons else []
