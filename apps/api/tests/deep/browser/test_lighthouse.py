"""Hız modülü gerçek Lighthouse + Chromium ile, yerel sitelerde (`pytest -m browser`).

- Lighthouse'un Chromium'u da tüm trafiği egress proxy'den geçirir: sayfanın doğrudan IP'ye,
  bulut metadata adresine veya iç ağa attığı istekler proxy'ye takılır (kanarya hiç bağlantı
  almaz), ölçüm yine de tamamlanır.
- Yoğun SPA'da beklenen bulgular çıkar; geçici profil/rapor klasörleri ölçüm sonrası silinir.
"""

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from launchcheck.checks.base import TargetType
from launchcheck.config import Settings
from launchcheck.deep.context import DeepContext
from launchcheck.deep.modules import ModuleOutput
from launchcheck.deep.perf_data import FieldStatus
from launchcheck.deep.perf_module import PerfModule
from launchcheck.security.ssrf import UrlGuard
from tests.deep import perf_sites
from tests.deep.browser.servers import Canary, LocalHttpServer, Route

pytestmark = pytest.mark.browser

if shutil.which("lighthouse") is None:
    pytest.skip("Lighthouse CLI yok (worker-deep imajında çalışır)", allow_module_level=True)


@pytest.fixture
def short_tmp() -> Iterator[Path]:
    """Chromium profil klasöründe Unix soketi açar (yol sınırı): pytest'in uzun tmp_path'i
    yerine kısa bir klasör (prod'da /tmp/launchcheck)."""
    path = Path(tempfile.mkdtemp(prefix="lc", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


async def resolve(host: str, port: int) -> list[str]:
    if host in ("site.test", "cdn.test"):
        return ["127.0.0.1"]
    if host == "internal.test":
        return ["10.0.0.5"]
    raise OSError("NXDOMAIN")


async def no_field(page: str, origin: str) -> tuple[str, None]:
    return FieldStatus.NO_KEY, None


def settings(tmp: Path) -> Settings:
    return Settings(
        deep_lighthouse_runs=1,
        deep_lighthouse_desktop_runs=0,
        deep_key_page_wait_s=0.1,
        scan_tmp_dir=str(tmp),
    )


async def run_module(
    site: LocalHttpServer, cdn: LocalHttpServer, tmp: Path
) -> tuple[DeepContext, ModuleOutput]:
    ctx = DeepContext(
        target_type=TargetType.WEB,
        target_label="http://site.test",
        target_url=f"http://site.test:{site.port}",
    )
    ctx.key_urls_ready.set()
    allow = frozenset({("127.0.0.1", site.port), ("127.0.0.1", cdn.port)})
    module = PerfModule(
        settings(tmp), guard=UrlGuard(resolver=resolve), field_query=no_field, allow=allow
    )
    out = ModuleOutput()
    await module.run(ctx, out)
    return ctx, out


async def test_heavy_spa_measured_through_proxy(short_tmp: Path) -> None:
    async with LocalHttpServer({}) as cdn:
        site_routes, cdn_routes = perf_sites.heavy_spa(f"http://cdn.test:{cdn.port}")
        cdn.routes.update(cdn_routes)
        async with LocalHttpServer(site_routes) as site:
            ctx, out = await run_module(site, cdn, short_tmp)
    assert ctx.perf is not None and len(ctx.perf.runs) == 1
    ids = {f.check_id for f in out.findings}
    assert {"web.perf.lcp_slow", "web.perf.unused_js", "web.perf.oversized_images"} <= ids
    assert "web.perf.blank_screen_long" in ids
    assert ctx.meta["perf"]["home_mobile"]["runs"] == 1
    assert list(short_tmp.iterdir()) == []  # Chromium profili ve rapor silindi


async def test_lighthouse_browser_cannot_bypass_proxy(short_tmp: Path) -> None:
    async with Canary() as canary:
        page = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Kanarya</title></head>
<body><h1>Merhaba dünya</h1><p>Sade bir sayfa.</p>
<img src="http://127.0.0.1:{canary.port}/direct.png" width="10" height="10">
<img src="http://internal.test/x.png" width="10" height="10">
<script>fetch('http://169.254.169.254/latest/meta-data/').catch(function(){{}});
fetch('http://127.0.0.1:{canary.port}/fetch').catch(function(){{}});</script>
</body></html>"""
        async with LocalHttpServer({"/": Route(page.encode())}) as site, LocalHttpServer({}) as cdn:
            ctx, _ = await run_module(site, cdn, short_tmp)
        assert canary.hits == 0
    assert ctx.perf is not None and len(ctx.perf.runs) == 1
    assert ctx.perf.runs[0].lcp_ms is not None
