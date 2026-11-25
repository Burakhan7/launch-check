"""Huni yürüyüşü gerçek Chromium ile, mobil görünümde (`pytest -m browser`).

Yerel siteler (gerçek ağ yok; `site.test` sadece testte 127.0.0.1'e çözülür):
1. funnel_saas: Vite benzeri SaaS, bilerek hatalı — uzun ve kartlı kayıt formu, yanlış alan
   türleri, fiyatlar "+KDV". Ana sayfada CTA'dan önce bir "Sepete ekle" düğmesi var: tıklanmamalı.
2. funnel_clean: düz HTML, doğru yapılmış → hiçbir huni bulgusu (yanlış alarm kontrolü).
3. funnel_wp: WordPress benzeri — CTA yavaş giriş sayfasına gidiyor, oradan kayıt linki (2 tık);
   "Premium" sayfası fiyat yerine "teklif alın" diyor.
Hiçbir formun gönderilmediği ve alanlara yazılmadığı sunucu kayıtlarıyla doğrulanır.
"""

from pathlib import Path
from typing import Any

import pytest

from launchcheck.checks.base import TargetType
from launchcheck.config import Settings
from launchcheck.deep.browser.funnel import FunnelCollector, FunnelInput, FunnelRaw
from launchcheck.deep.browser.launcher import BrowserSession
from launchcheck.deep.context import DeepContext
from launchcheck.deep.funnel_module import FunnelModule
from launchcheck.deep.modules import ModuleOutput
from launchcheck.security.ssrf import UrlGuard
from tests.deep.browser.servers import LocalHttpServer, Route

pytestmark = pytest.mark.browser
pytest.importorskip("playwright")

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "deep"
UA = "LaunchCheckBot/1.0 (+https://launchcheck.example/bot)"
SETTINGS = Settings()


async def resolve(host: str, port: int) -> list[str]:
    if host != "site.test":
        raise OSError("NXDOMAIN")
    return ["127.0.0.1"]


def html(path: str, delay_s: float = 0) -> Route:
    return Route((FIXTURES / path).read_bytes(), delay_s=delay_s)


async def walk(
    routes: dict[str, Route], pricing: str | None = None
) -> tuple[FunnelRaw, list[bytes], int]:
    shots: list[str] = []
    async with LocalHttpServer(routes) as server:
        base = f"http://site.test:{server.port}"

        def save(kind: str, url: str, data: bytes, width: int, height: int) -> None:
            assert kind == "funnel_step" and data[:4] == b"RIFF" and width <= 1440
            shots.append(url)

        data = FunnelInput(
            home_url=base + "/",
            signup_candidates=[],
            pricing_candidates=[(base + pricing, 1)] if pricing else [],
            save_screenshot=save,  # type: ignore[arg-type]
        )
        allow = frozenset({("127.0.0.1", server.port)})
        async with BrowserSession.open(UrlGuard(resolver=resolve), UA, allow=allow) as session:
            raw = await FunnelCollector(SETTINGS).collect(session, data)
        port = server.port
        requests = list(server.requests)
    assert len(shots) == len(raw.steps)
    return raw, requests, port


async def findings(raw: FunnelRaw, port: int) -> tuple[DeepContext, dict[str, Any]]:
    async def fake(ctx: DeepContext, data_in: FunnelInput) -> FunnelRaw:
        return raw

    target = f"http://site.test:{port}"
    ctx = DeepContext(target_type=TargetType.WEB, target_label=target, target_url=target)
    out = ModuleOutput()
    await FunnelModule(SETTINGS, collect=fake).run(ctx, out)
    return ctx, {f.check_id: f for f in out.findings}


def assert_passive(requests: list[bytes]) -> None:
    """Form gönderilmedi (POST yok, action adresine istek yok)."""
    assert not any(r.startswith(b"POST") for r in requests)
    assert not any(b"/never" in r for r in requests)


async def test_saas_funnel() -> None:
    raw, requests, port = await walk(
        {
            "/": html("funnel_saas/index.html"),
            "/signup": html("funnel_saas/signup.html"),
            "/pricing": html("funnel_saas/pricing.html"),
        },
        pricing="/pricing",
    )
    assert_passive(requests)
    assert raw.cta_text == "Ücretsiz başla"  # "Sepete ekle" atlandı
    assert [s.kind for s in raw.steps] == ["home", "signup", "pricing"]
    assert raw.steps[1].clicks == 1 and raw.steps[1].load_ms is not None
    _, found = await findings(raw, port)
    friction = found["web.funnel.signup_friction"].evidence
    assert friction["fields"] == 8 and friction["required"] == 7
    assert "card_required" in friction["issues"]
    kinds = {f["kind"] for f in found["web.funnel.mobile_input_types"].evidence["fields"]}
    assert {"email", "tel", "name"} <= kinds
    vat = found["web.funnel.vat_excluded_price"].evidence
    assert vat["phrases"] == ["+kdv"]  # SSS'teki "KDV ayrıca" fiyatın yanında değil
    assert "web.funnel.price_hidden" not in found
    assert "web.funnel.too_many_steps" not in found


async def test_clean_funnel_has_no_findings() -> None:
    raw, requests, port = await walk(
        {
            "/": html("funnel_clean/index.html"),
            "/signup": html("funnel_clean/signup.html"),
            "/pricing": html("funnel_clean/pricing.html"),
        },
        pricing="/pricing",
    )
    assert_passive(requests)
    assert [s.kind for s in raw.steps] == ["home", "signup", "pricing"]
    ctx, found = await findings(raw, port)
    assert found == {}, {k: v.evidence for k, v in found.items()}
    assert ctx.meta["funnel"]["signup"]["social"] == ["google"]


async def test_wordpress_login_first_and_slow() -> None:
    raw, requests, port = await walk(
        {
            "/": html("funnel_wp/index.html"),
            "/giris": html("funnel_wp/login.html", delay_s=3.5),
            "/kayit-ol": html("funnel_wp/register.html"),
            "/premium": html("funnel_wp/premium.html"),
        },
        pricing="/premium",
    )
    assert_passive(requests)
    assert [(s.kind, s.clicks) for s in raw.steps] == [
        ("home", 0),
        ("login", 1),
        ("signup", 2),
        ("pricing", 1),
    ]
    assert (raw.steps[1].load_ms or 0) >= 3000
    ctx, found = await findings(raw, port)
    steps = found["web.funnel.too_many_steps"].evidence
    assert steps["issues"] == ["slow_step"]
    hidden = found["web.funnel.price_hidden"]
    assert hidden.evidence["quote_based"] is True and str(hidden.severity) == "low"
    assert "web.funnel.signup_friction" not in found  # kısa form, Google ile giriş var
    assert [s["kind"] for s in ctx.meta["funnel"]["steps"]] == [
        "home",
        "login",
        "signup",
        "pricing",
    ]
