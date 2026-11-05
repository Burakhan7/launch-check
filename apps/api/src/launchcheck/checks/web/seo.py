"""Arama motorlarının siteyi görmesini engelleyen, yayında unutulmuş ayarlar."""

import re

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import WebCheck, html_tree, is_html
from launchcheck.collectors.web_collector import WebContext

ROBOTS_META_NAMES = ("robots", "googlebot")


@register
class NoindexLeft(WebCheck):
    id = "web.seo.noindex_left"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: WebContext) -> list[Finding]:
        sources = []
        if "noindex" in ctx.headers.get("x-robots-tag", "").lower():
            sources.append("x-robots-tag")
        for node in html_tree(ctx).css("meta[name]"):
            name = (node.attributes.get("name") or "").lower()
            content = (node.attributes.get("content") or "").lower()
            if name in ROBOTS_META_NAMES and "noindex" in content:
                sources.append(f"meta[name={name}]")
        return [self.finding({"sources": sources})] if sources else []


def blocks_everything(robots_txt: str) -> bool:
    """`User-agent: *` grubunda `Disallow: /` var ve kök için `Allow` yok mu?"""
    agents: list[str] = []
    in_rules = False
    disallow_root = allow_root = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:  # yeni grup başlıyor
                agents, in_rules = [], False
            agents.append(value)
        elif key in ("disallow", "allow"):
            in_rules = True
            if "*" in agents and value == "/":
                if key == "disallow":
                    disallow_root = True
                else:
                    allow_root = True
    return disallow_root and not allow_root


@register
class RobotsDisallowAll(WebCheck):
    id = "web.seo.robots_disallow_all"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: WebContext) -> list[Finding]:
        res = await ctx.real_file("/robots.txt")
        if res is None or is_html(res) or not re.search(rb"(?i)user-agent", res.body):
            return []
        return [self.finding({"path": "/robots.txt"})] if blocks_everything(res.text) else []
