"""Lighthouse rapor fixture'larını yeniden kaydeder (Lighthouse sürümü değişince çalıştır).

worker-deep imajında, apps/api klasöründen:
    python -m tests.deep.record_lighthouse

Yerel sitelerden (`perf_sites.py`) gerçek Lighthouse ölçümü alır, raporu kırpar (kullandığımız
denetimler + küçük kareler) ve `tests/fixtures/deep/lighthouse/*.json`'a yazar.
"""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from launchcheck.config import Settings
from launchcheck.deep.browser.egress_proxy import EgressProxy
from launchcheck.deep.perf.lighthouse import LighthouseConfig, LighthouseRunner
from launchcheck.security.ssrf import UrlGuard
from tests.deep import perf_sites
from tests.deep.browser.servers import LocalHttpServer, Route

Sites = tuple[dict[str, Route], dict[str, Route]]

OUT = Path(__file__).parent.parent / "fixtures" / "deep" / "lighthouse"
DETAIL_AUDITS = {
    "metrics",
    "network-requests",
    "resource-summary",
    "screenshot-thumbnails",
    "unused-javascript",
    "image-delivery-insight",
    "unsized-images",
    "cache-insight",
    "third-parties-insight",
}
VALUE_AUDITS = {
    "largest-contentful-paint",
    "cumulative-layout-shift",
    "first-contentful-paint",
    "speed-index",
    "total-blocking-time",
}


def trim(report: dict[str, Any]) -> dict[str, Any]:
    audits = {}
    for audit_id, audit in report["audits"].items():
        kept = {k: audit.get(k) for k in ("score", "scoreDisplayMode", "metricSavings")}
        if audit_id in VALUE_AUDITS | DETAIL_AUDITS:
            kept["numericValue"] = audit.get("numericValue")
        if audit_id in DETAIL_AUDITS:
            kept["details"] = audit.get("details")
        audits[audit_id] = kept
    return {
        "lighthouseVersion": report.get("lighthouseVersion"),
        "finalDisplayedUrl": report.get("finalDisplayedUrl"),
        "mainDocumentUrl": report.get("mainDocumentUrl"),
        "runtimeError": report.get("runtimeError"),
        "categories": {k: {"score": v.get("score")} for k, v in report["categories"].items()},
        "audits": audits,
    }


async def resolve(host: str, port: int) -> list[str]:
    if host not in ("site.test", "cdn.test"):
        raise OSError("NXDOMAIN")
    return ["127.0.0.1"]


async def measure(build: Callable[[str], Sites], form_factor: str = "mobile") -> dict[str, Any]:
    """`build(cdn_origin)` → (site rotaları, cdn rotaları). İki yerel sunucu + egress proxy."""
    async with LocalHttpServer({}) as cdn:
        site_routes, cdn_routes = build(f"http://cdn.test:{cdn.port}")
        cdn.routes.update(cdn_routes)
        async with LocalHttpServer(site_routes) as site:
            allow = frozenset({("127.0.0.1", site.port), ("127.0.0.1", cdn.port)})
            proxy = EgressProxy(UrlGuard(resolver=resolve), 200 * 1024 * 1024, allow=allow)
            await proxy.start()
            try:
                runner = LighthouseRunner(LighthouseConfig.from_settings(Settings()), proxy.port)
                return await runner.run(f"http://site.test:{site.port}/", form_factor)
            finally:
                await proxy.close()


SITES: dict[str, Callable[[str], Sites]] = {
    "heavy_spa": perf_sites.heavy_spa,
    "plain_clean": lambda _cdn: (perf_sites.plain_clean(), {}),
    "wp_like": lambda _cdn: (perf_sites.wp_like(), {}),
}


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in SITES.items():
        report = await measure(build)
        (OUT / f"{name}.json").write_text(json.dumps(trim(report), ensure_ascii=False), "utf-8")
        scores = {k: v["score"] for k, v in report["categories"].items()}
        print(name, report.get("runtimeError"), scores)


if __name__ == "__main__":
    asyncio.run(main())
