"""Dış görünürlük modülü (P5): crt.sh, DNS (ele geçirme, SPF/DMARC), Wayback, RDAP, sertifika.

Dış servisler kayıtlı yanıtlarla (respx, tests/fixtures/deep/external) taklit edilir; DNS sahte
bir istemciyle. Her servis için hata ve zaman aşımı senaryosu var. Alt alan adlarında gerçek
Faz 1 check'leri çalışır (farklı yapılarda sahte siteler).
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.api.report import _passed
from launchcheck.checks.base import Severity, TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.config import Settings
from launchcheck.db.models import FindingRow, Scan
from launchcheck.deep.context import DeepContext
from launchcheck.deep.events import MemoryEventSink
from launchcheck.deep.external.dns import DnsError, SystemDns, _text, cname_chain
from launchcheck.deep.external.email_dns import all_qualifier, collect_email, dmarc_policy
from launchcheck.deep.external.rdap import parse_expiry, parse_peercert
from launchcheck.deep.external.services import NoLimit, RedisServiceLimiter
from launchcheck.deep.external.subdomains import is_priority, parse_names, select_candidates
from launchcheck.deep.external.takeover import assess, load_fingerprints
from launchcheck.deep.external.wayback import parse_rows, sensitive_paths
from launchcheck.deep.external_data import (
    ArchivedPath,
    CertInfo,
    DomainExpiry,
    EmailDns,
    ExternalData,
    PartStatus,
    SubdomainScan,
    SubFinding,
    TakeoverRisk,
)
from launchcheck.deep.external_module import ExternalModule, ExternalUnavailableError, summary
from launchcheck.deep.modules import ModuleOutput
from launchcheck.deep.orchestrator import DeepOrchestrator, OrchestratorConfig
from launchcheck.i18n import finding_text, fragment_fn
from launchcheck.security.ssrf import UrlGuard
from tests.conftest import FakeRedis, FakeResolver
from tests.deep.helpers import add_deep, add_parent

FIXTURES = Path(__file__).parent.parent / "fixtures" / "deep" / "external"
TARGET = "https://www.example.com"
PUBLIC = "93.184.216.34"
EXTERNAL_IDS = {
    "web.external.forgotten_subdomain",
    "web.external.subdomain_takeover_risk",
    "web.external.email_spoofing",
    "web.external.archived_sensitive_paths",
    "web.external.domain_expiry_soon",
    "web.external.cert_expiry_soon",
}
HTML = {"content-type": "text/html; charset=utf-8"}
TEXT = {"content-type": "text/plain"}


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def rdap_payload(days: float) -> dict[str, Any]:
    expires = (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = (FIXTURES / "rdap.json").read_text(encoding="utf-8").replace("__EXPIRES__", expires)
    return json.loads(text)


class FakeDns:
    """(ad, tip) → kayıtlar. Tanımsız A → herkese açık IP; tanımsız diğerleri → kayıt yok.
    `None` değer NXDOMAIN; `fail` içindeki adlar sunucu hatası verir."""

    def __init__(
        self, records: dict[tuple[str, str], list[str] | None], fail: set[str] | None = None
    ) -> None:
        self.records = records
        self.fail = fail or set()
        self.queries: list[tuple[str, str]] = []

    async def query(self, name: str, rdtype: str) -> list[str] | None:
        self.queries.append((name, rdtype))
        if name in self.fail:
            raise DnsError("SERVFAIL")
        if (name, rdtype) in self.records:
            value = self.records[(name, rdtype)]
            return list(value) if value is not None else None
        if rdtype == "A":
            return [PUBLIC]
        return []


GOOD_EMAIL = {
    ("example.com", "MX"): ["aspmx.l.google.com"],
    ("example.com", "TXT"): ["google-site-verification=abc", "v=spf1 include:_spf.google.com -all"],
    ("_spf.google.com", "TXT"): ["v=spf1 include:_netblocks.google.com ~all"],
    ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=reject; rua=mailto:d@example.com"],
}


def site_dns() -> FakeDns:
    return FakeDns(
        {
            **GOOD_EMAIL,
            ("gone.example.com", "CNAME"): ["myapp-old.herokuapp.com"],
            ("docs.example.com", "CNAME"): ["acme.github.io"],
            ("legacy.example.com", "CNAME"): ["legacy-app.azurewebsites.net"],
            ("legacy.example.com", "A"): None,
            ("legacy-app.azurewebsites.net", "A"): None,
            ("cdn.example.com", "CNAME"): ["cname.vercel-dns.com"],
            ("internal.example.com", "A"): ["10.0.0.5"],
            ("mail.example.com", "A"): None,
        }
    )


# --- sahte alt alan adı siteleri (farklı yapılar)


def _handler(routes: dict[str, httpx.Response], fallback: httpx.Response) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        return routes.get(request.url.path, fallback)

    return handle


NEXT_SPA = (
    '<!doctype html><html><head><title>Staging</title><script id="__NEXT_DATA__" '
    'type="application/json">{"page":"/"}</script></head><body><div id="__next"></div></body></html>'
)
WP_HOME = (
    '<!doctype html><html><head><meta name="generator" content="WordPress 5.2">'
    "<title>Eski blog</title></head><body><h1>Eski</h1></body></html>"
)
PLAIN = (
    "<!doctype html><html><head><title>Blog</title></head><body><h1>Blog</h1>"
    "<p>Yazılar.</p></body></html>"
)
PREVIEW = (
    '<!doctype html><html><head><meta name="robots" content="noindex"><title>Preview</title>'
    '</head><body><div id="root"></div></body></html>'
)


def mock_world(router: respx.MockRouter, *, crtsh: Any = None, wayback: Any = None) -> None:
    """crt.sh, Wayback, RDAP + hedef ve alt alan adı siteleri."""
    router.get(host="crt.sh").mock(
        return_value=httpx.Response(200, json=fixture("crtsh.json") if crtsh is None else crtsh)
    )
    router.get(host="web.archive.org").mock(
        return_value=httpx.Response(
            200, json=fixture("wayback_cdx.json") if wayback is None else wayback
        )
    )
    router.get(host="rdap.org").mock(return_value=httpx.Response(200, json=rdap_payload(400)))
    # Yedek kaynak: varsayılan olarak o da kapalı (crt.sh hatası testlerinde parça düşsün)
    router.get(host="api.certspotter.com").mock(return_value=httpx.Response(503))
    redirect = lambda host: httpx.Response(301, headers={"location": f"https://{host}/"})  # noqa: E731
    # Next.js SPA (her yola index.html), .env açık
    router.route(host="staging.example.com", scheme="http").mock(
        return_value=redirect("staging.example.com")
    )
    router.route(host="staging.example.com", scheme="https").mock(
        side_effect=_handler(
            {"/.env": httpx.Response(200, text="DATABASE_URL=postgres://u:p@h/db\nSTRIPE_KEY=x\n")},
            httpx.Response(200, text=NEXT_SPA, headers=HTML),
        )
    )
    # WordPress, sadece HTTP (443 kapalı), veritabanı yedeği açık
    router.route(host="old.example.com", scheme="https").mock(
        side_effect=httpx.ConnectError("refused")
    )
    router.route(host="old.example.com", scheme="http").mock(
        side_effect=_handler(
            {
                "/": httpx.Response(200, text=WP_HOME, headers=HTML),
                "/backup.sql": httpx.Response(
                    200, text="-- MySQL dump 10.13\nCREATE TABLE wp_users (id int);", headers=TEXT
                ),
            },
            httpx.Response(404, text="not found", headers=TEXT),
        )
    )
    # API: ana sayfa 404 JSON, .git açık
    router.route(host="api.example.com", scheme="http").mock(
        return_value=redirect("api.example.com")
    )
    router.route(host="api.example.com", scheme="https").mock(
        side_effect=_handler(
            {
                "/.git/HEAD": httpx.Response(200, text="ref: refs/heads/main\n", headers=TEXT),
                "/.git/config": httpx.Response(
                    200, text='[core]\n\trepositoryformatversion = 0\n[remote "origin"]\n'
                ),
            },
            httpx.Response(404, json={"error": "not_found"}),
        )
    )
    # Temiz düz HTML blog (yanlış alarm olmamalı)
    router.route(host="blog.example.com", scheme="http").mock(
        return_value=redirect("blog.example.com")
    )
    router.route(host="blog.example.com", scheme="https").mock(
        side_effect=_handler(
            {"/": httpx.Response(200, text=PLAIN, headers=HTML)},
            httpx.Response(404, text="Not found", headers=TEXT),
        )
    )
    # Önizleme: noindex SPA, başka sorun yok (sadece bilgi → bulgu değil)
    router.route(host="preview.example.com", scheme="http").mock(
        return_value=redirect("preview.example.com")
    )
    router.route(host="preview.example.com", scheme="https").mock(
        return_value=httpx.Response(200, text=PREVIEW, headers=HTML)
    )
    # Ele geçirme adayları (HTTP → HTTPS yönlendiren servisler)
    for host, response in (
        ("gone.example.com", httpx.Response(404, text="<html>No such app</html>", headers=HTML)),
        (
            "docs.example.com",
            httpx.Response(
                404, text="<h1>404</h1><p>There isn't a GitHub Pages site here.</p>", headers=HTML
            ),
        ),
        ("cdn.example.com", httpx.Response(200, text=PLAIN, headers=HTML)),
    ):
        router.route(host=host, scheme="http").mock(return_value=redirect(host))
        router.route(host=host, scheme="https").mock(return_value=response)
    # Hedef site (arşiv yolları): SPA catch-all, sadece .env gerçekten açık
    router.route(host="www.example.com").mock(
        side_effect=_handler(
            {
                "/.env": httpx.Response(200, text="API_SECRET=abc\nNODE_ENV=production\n"),
                "/.git/config": httpx.Response(404, text="nope", headers=TEXT),
                "/old/config.json": httpx.Response(404, text="nope", headers=TEXT),
                "/swagger.json": httpx.Response(404, text="nope", headers=TEXT),
            },
            httpx.Response(200, text=NEXT_SPA, headers=HTML),
        )
    )


def ext_settings(**kw: Any) -> Settings:
    return Settings(deep_subdomain_scan_timeout_s=20, **kw)


def module(dns: FakeDns | None = None, **kw: Any) -> ExternalModule:
    return ExternalModule(
        ext_settings(),
        guard=UrlGuard(FakeResolver()),  # type: ignore[arg-type]
        dns=dns or site_dns(),
        limiter=NoLimit(),
        cert_probe=kw.pop("cert_probe", None) or cert_probe(days_left=60),
        **kw,
    )


def cert_probe(days_left: float, lifetime_days: float = 90) -> Any:
    async def probe(host: str) -> CertInfo:
        not_after = datetime.now(UTC) + timedelta(days=days_left)
        return CertInfo(host, not_after - timedelta(days=lifetime_days), not_after, "Let's Encrypt")

    return probe


def ctx_for(target: str = TARGET, sink: MemoryEventSink | None = None) -> DeepContext:
    return DeepContext(
        target_type=TargetType.WEB,
        target_label=target,
        target_url=target,
        events=sink or MemoryEventSink(),
    )


async def run_module(mod: ExternalModule, target: str = TARGET) -> tuple[DeepContext, ModuleOutput]:
    ctx = ctx_for(target)
    out = ModuleOutput()
    await mod.run(ctx, out)
    return ctx, out


def by_id(out: ModuleOutput) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = {}
    for f in out.findings:
        found.setdefault(f.check_id, []).append(f)
    return found


# --- crt.sh ayrıştırma ve aday seçimi


def test_parse_names_dedupes_and_drops_wildcards_emails_and_foreign() -> None:
    names = parse_names(fixture("crtsh.json"), "example.com")
    assert "api.example.com" in names and names.count("api.example.com") == 1
    assert not any("*" in n or "@" in n for n in names)
    assert "notexample.com" not in names and "example.com.evil.net" not in names
    assert parse_names({"error": "x"}, "example.com") == []


def test_candidates_put_priority_names_first_and_skip_target_twins() -> None:
    names = parse_names(fixture("crtsh.json"), "example.com")
    ranked = select_candidates(
        names, "www.example.com", "example.com", Settings().deep_subdomain_priority_words, 60
    )
    hosts = [h for h, _ in ranked]
    assert "www.example.com" not in hosts and "example.com" not in hosts
    priority = [h for h, p in ranked if p]
    assert set(priority) == {
        "staging.example.com",
        "old.example.com",
        "api.example.com",
        "preview.example.com",
    }
    assert hosts[: len(priority)] == priority


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("api-dev.example.com", True),
        ("eski.example.com.tr", True),
        ("deneme2.example.com", True),
        ("shop.example.com", False),
        ("blog.example.com", False),
    ],
)
def test_priority_words_tr_and_en(host: str, expected: bool) -> None:
    domain = "example.com.tr" if host.endswith(".tr") else "example.com"
    assert is_priority(host, domain, Settings().deep_subdomain_priority_words) is expected


# --- DNS yardımcıları


async def test_cname_chain_follows_hops_and_stops_on_loop() -> None:
    dns = FakeDns(
        {
            ("a.example.com", "CNAME"): ["b.example.net"],
            ("b.example.net", "CNAME"): ["a.example.com"],
            ("c.example.com", "CNAME"): ["x.herokudns.com"],
            ("x.herokudns.com", "CNAME"): ["y.herokuapp.com"],
        }
    )
    assert await cname_chain(dns, "a.example.com") == ["b.example.net"]
    assert await cname_chain(dns, "c.example.com") == ["x.herokudns.com", "y.herokuapp.com"]
    assert await cname_chain(dns, "plain.example.com") == []


def test_system_dns_text_conversion() -> None:
    import dns.rdata
    import dns.rdataclass
    import dns.rdatatype

    txt = dns.rdata.from_text(
        dns.rdataclass.IN, dns.rdatatype.TXT, '"v=spf1 include:a.com" " -all"'
    )
    mx = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.MX, "10 MX.Example.COM.")
    cname = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.CNAME, "App.Vercel-DNS.com.")
    null_mx = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.MX, "0 .")
    assert _text(txt, "TXT") == "v=spf1 include:a.com -all"
    assert _text(mx, "MX") == "mx.example.com"
    assert _text(cname, "CNAME") == "app.vercel-dns.com"
    assert _text(null_mx, "MX") == ""
    assert SystemDns(2.0)._get().lifetime == 2.0


# --- ele geçirme


def page(body: str | None) -> Any:
    async def fetch(host: str) -> str | None:
        return body

    return fetch


async def test_takeover_fingerprint_nxdomain_and_working_sites() -> None:
    dns = site_dns()
    heroku = await assess("gone.example.com", ["myapp-old.herokuapp.com"], dns, page("No such app"))
    assert heroku == TakeoverRisk(
        "gone.example.com", "myapp-old.herokuapp.com", "Heroku", "fingerprint", "edge"
    )
    azure = await assess("legacy.example.com", ["legacy-app.azurewebsites.net"], dns, page(None))
    assert azure is not None and azure.reason == "nxdomain" and azure.confidence == "vulnerable"
    s3 = await assess(
        "files.example.com",
        ["files.example.com.s3-website-us-east-1.amazonaws.com"],
        dns,
        page("<Code>NoSuchBucket</Code><Message>The specified bucket does not exist</Message>"),
    )
    assert s3 is not None and s3.service == "AWS S3"


async def test_takeover_false_positives() -> None:
    dns = site_dns()
    # Servise yönelen ama çalışan site
    assert await assess("cdn.example.com", ["cname.vercel-dns.com"], dns, page(PLAIN)) is None
    # NXDOMAIN tipi servis ama hedef var
    assert await assess("app.example.com", ["app.azurewebsites.net"], dns, page(None)) is None
    # Parmak izi metni geçiyor ama CNAME bilinen bir servise değil (kendi sunucusu)
    assert await assess("x.example.com", ["lb.example.net"], dns, page("No such app")) is None
    # Sayfaya ulaşılamıyor → kanıt yok
    assert await assess("docs.example.com", ["acme.github.io"], dns, page(None)) is None


def test_fingerprints_file_is_well_formed() -> None:
    fps = load_fingerprints()
    assert len(fps) >= 25
    for fp in fps:
        assert fp.cnames, fp.service
        assert fp.nxdomain or fp.patterns, fp.service
        assert fp.confidence in ("vulnerable", "edge")


# --- e-posta DNS


@pytest.mark.parametrize(
    ("records", "problems", "severity"),
    [
        # Google Workspace, iyi yapılandırılmış (yanlış alarm yok)
        (GOOD_EMAIL, None, None),
        # Microsoft 365, SPF ~all + DMARC quarantine (yanlış alarm yok)
        (
            {
                ("example.com", "MX"): ["example-com.mail.protection.outlook.com"],
                ("example.com", "TXT"): ["v=spf1 include:spf.protection.outlook.com ~all"],
                ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=quarantine; pct=100"],
            },
            None,
            None,
        ),
        # E-posta almayan alan adı, doğru kilitlenmiş: null MX + -all + reject (yanlış alarm yok)
        (
            {
                ("example.com", "MX"): [""],
                ("example.com", "TXT"): ["v=spf1 -all"],
                ("_dmarc.example.com", "TXT"): ["v=DMARC1;p=reject"],
            },
            None,
            None,
        ),
        # Küçük işletme: MX var, SPF var, DMARC yok
        (
            {
                ("example.com", "MX"): ["mx1.yandex.net"],
                ("example.com", "TXT"): ["v=spf1 redirect=_spf.yandex.net"],
                ("_spf.yandex.net", "TXT"): ["v=spf1 ip4:77.88.0.0/18 ~all"],
            },
            ["dmarc_missing"],
            Severity.HIGH,
        ),
        # SPF +all ve DMARC p=none
        (
            {
                ("example.com", "MX"): ["mail.example.com"],
                ("example.com", "TXT"): ["v=spf1 a mx +all"],
                ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=none"],
            },
            ["spf_pass_all", "dmarc_none"],
            Severity.HIGH,
        ),
        # Hiç kayıt yok, e-posta da almıyor (park edilmiş alan adı)
        ({}, ["spf_missing", "dmarc_missing"], Severity.LOW),
        # İki SPF kaydı (ikisi de geçersiz sayılır), DMARC sıkı
        (
            {
                ("example.com", "MX"): ["mx.example.com"],
                ("example.com", "TXT"): ["v=spf1 include:a.net -all", "v=spf1 include:b.net -all"],
                ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=reject"],
            },
            ["spf_multiple"],
            Severity.MEDIUM,
        ),
    ],
)
async def test_email_spoofing_configs(
    records: dict[tuple[str, str], list[str] | None],
    problems: list[str] | None,
    severity: Severity | None,
) -> None:
    data = ExternalData("www.example.com", "example.com", False)
    data.email = await collect_email(FakeDns(records), "example.com", 10)
    findings = await _check("web.external.email_spoofing", data)
    if problems is None:
        assert findings == []
    else:
        assert len(findings) == 1
        assert findings[0].evidence["problems"] == problems
        assert findings[0].severity == severity


async def test_spf_lookup_limit_counts_nested_includes() -> None:
    includes = " ".join(f"include:s{i}.example.net" for i in range(6))
    records: dict[tuple[str, str], list[str] | None] = {
        ("example.com", "MX"): ["mx.example.com"],
        ("example.com", "TXT"): [f"v=spf1 {includes} -all"],
        ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=reject"],
    }
    for i in range(6):
        records[(f"s{i}.example.net", "TXT")] = ["v=spf1 a mx ~all"]
    email = await collect_email(FakeDns(records), "example.com", 10)
    assert email.spf_lookups > 10 and email.spf_all == "-all"
    data = ExternalData("www.example.com", "example.com", False)
    data.email = email
    (finding,) = await _check("web.external.email_spoofing", data)
    assert finding.evidence["problems"] == ["spf_too_many_lookups"]


def test_spf_and_dmarc_parsing() -> None:
    assert all_qualifier("v=spf1 include:x.com -all") == "-all"
    assert all_qualifier("v=spf1 all") == "+all"
    assert all_qualifier("v=spf1 ip4:1.2.3.4") is None
    assert dmarc_policy("v=DMARC1; p = Quarantine ; rua=mailto:x@y") == "quarantine"
    assert dmarc_policy("v=DMARC1; rua=mailto:x@y") is None


async def test_email_dns_server_failure_raises() -> None:
    with pytest.raises(DnsError):
        await collect_email(FakeDns({}, fail={"example.com"}), "example.com", 10)


# --- Wayback


def test_sensitive_paths_from_cdx() -> None:
    rows = parse_rows(fixture("wayback_cdx.json"))
    assert len(rows) == 12
    items = sensitive_paths(rows, "www.example.com", Settings().deep_wayback_patterns)
    assert [(p, k) for p, k, _ in items] == [
        ("/.env", "env"),
        ("/.git/config", "git"),
        ("/backup/db_2020.sql", "sql"),
        ("/old/config.json", "config"),
        ("/swagger.json", "api_docs"),
    ]
    assert items[0][2] == "20210115"  # aynı yolun en yeni kaydı
    paths = {p for p, _, _ in items}
    # Yanlış alarm: admin/API sayfaları, içinde ".env" geçen blog yazısı, başka alt alan adı
    assert not paths & {"/wp-admin/", "/api/users", "/blog/how-to-configure-env-files"}


def test_wayback_patterns_wordpress_and_tr_names() -> None:
    rows = [
        ("http://example.com.tr/wp-config.php.bak", "20200101000000"),
        ("http://example.com.tr/yedek_2021.zip", "20210101000000"),
        ("http://example.com.tr/phpinfo.php", "20190101000000"),
        ("http://example.com.tr/wp-content/uploads/2021/05/photo.jpg", "20210101000000"),
        ("http://example.com.tr/blog/backup-strategies/", "20210101000000"),
    ]
    items = sensitive_paths(rows, "example.com.tr", Settings().deep_wayback_patterns)
    kinds = {p: k for p, k, _ in items}
    assert kinds == {"/yedek_2021.zip": "backup", "/phpinfo.php": "phpinfo"}


# --- RDAP ve sertifika


def test_parse_expiry_and_peercert() -> None:
    expires = parse_expiry(rdap_payload(10))
    assert expires is not None and expires.tzinfo is not None
    assert parse_expiry({"events": [{"eventAction": "registration", "eventDate": "x"}]}) is None
    assert parse_expiry(None) is None
    cert = parse_peercert(
        "www.example.com",
        {
            "notBefore": "Oct  1 00:00:00 2026 GMT",
            "notAfter": "Dec 30 00:00:00 2026 GMT",
            "issuer": ((("countryName", "US"),), (("organizationName", "Let's Encrypt"),)),
        },
    )
    assert cert is not None and cert.issuer == "Let's Encrypt"
    assert (cert.not_after - cert.not_before).days == 90


@pytest.mark.parametrize(
    ("days", "severity"),
    [(20, Severity.HIGH), (45, Severity.MEDIUM), (200, None), (-3, Severity.HIGH)],
)
async def test_domain_expiry(days: float, severity: Severity | None) -> None:
    data = ExternalData("www.example.com", "example.com", False)
    data.domain_expiry = DomainExpiry("example.com", datetime.now(UTC) + timedelta(days=days))
    findings = await _check("web.external.domain_expiry_soon", data)
    assert [f.severity for f in findings] == ([severity] if severity else [])


@pytest.mark.parametrize(
    ("days_left", "lifetime", "severity"),
    [
        (10, 398, Severity.HIGH),  # ticari 1 yıllık sertifika
        (20, 398, Severity.MEDIUM),
        (25, 90, Severity.MEDIUM),  # Let's Encrypt 90 gün: 30 günde yenilenmeliydi
        (10, 90, Severity.HIGH),
        (28, 90, None),  # normal yenileme penceresi (yanlış alarm yok)
        (3, 6, None),  # 6 günlük kısa ömürlü sertifika (yanlış alarm yok)
        (14, 45, None),  # 45 günlük sertifika, yenileme penceresinin (15 gün) hemen içinde
        (11, 45, Severity.MEDIUM),  # 45 günlük sertifika, yenileme birkaç gün gecikmiş
        (-1, 90, Severity.HIGH),
    ],
)
async def test_cert_expiry_scales_with_lifetime(
    days_left: float, lifetime: float, severity: Severity | None
) -> None:
    data = ExternalData("www.example.com", "example.com", False)
    data.cert = await cert_probe(days_left, lifetime)("www.example.com")
    findings = await _check("web.external.cert_expiry_soon", data)
    assert [f.severity for f in findings] == ([severity] if severity else [])


# --- check'ler doğrudan veriyle


async def _check(check_id: str, data: ExternalData) -> list[Any]:
    ctx = ctx_for()
    ctx.external = data
    check = load_builtin_checks().get(check_id)
    assert check is not None
    return await check.run(ctx)


async def test_forgotten_subdomain_uses_worst_severity_and_ignores_info_only() -> None:
    data = ExternalData("www.example.com", "example.com", False)
    data.subdomains = [
        SubdomainScan(
            "staging.example.com",
            "https://staging.example.com/",
            True,
            [
                SubFinding("web.seo.noindex_left", "high", {}),
                SubFinding("web.https.missing", "high", {"reason": "no_redirect"}),
                SubFinding("web.files.env_exposed", "critical", {"path": "/.env", "keys": ["A"]}),
            ],
        ),
        SubdomainScan(
            "preview.example.com",
            "https://preview.example.com/",
            True,
            [SubFinding("web.seo.noindex_left", "high", {})],
        ),
        SubdomainScan("blog.example.com", "https://blog.example.com/", False, []),
    ]
    findings = await _check("web.external.forgotten_subdomain", data)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == Severity.CRITICAL and f.evidence["host"] == "staging.example.com"
    assert [c["check_id"] for c in f.evidence["checks"]] == [
        "web.files.env_exposed",
        "web.https.missing",
    ]
    assert f.evidence["info_checks"] == ["web.seo.noindex_left"]


async def test_archived_paths_live_high_and_gone_info() -> None:
    data = ExternalData("www.example.com", "example.com", False)
    data.archived = [
        ArchivedPath("/.env", "env", "20210115", True),
        ArchivedPath("/backup.sql", "sql", "20200101", False),
    ]
    findings = await _check("web.external.archived_sensitive_paths", data)
    assert [(f.severity, f.evidence["state"]) for f in findings] == [
        (Severity.HIGH, "live"),
        (Severity.INFO, "gone"),
    ]
    data.archived = []
    assert await _check("web.external.archived_sensitive_paths", data) == []


async def test_takeover_severity_by_confidence() -> None:
    data = ExternalData("www.example.com", "example.com", False)
    data.takeovers = [
        TakeoverRisk(
            "a.example.com", "a.azurewebsites.net", "Microsoft Azure", "nxdomain", "vulnerable"
        ),
        TakeoverRisk("b.example.com", "b.github.io", "GitHub Pages", "fingerprint", "edge"),
    ]
    findings = await _check("web.external.subdomain_takeover_risk", data)
    assert [f.severity for f in findings] == [Severity.HIGH, Severity.MEDIUM]


def test_external_checks_are_phase2_only() -> None:
    registry = load_builtin_checks()
    ids = {c.id for c in registry.all() if c.module == "external"}
    assert ids == EXTERNAL_IDS
    assert not ids & {c.id for c in registry.for_target(TargetType.WEB, 1)}
    assert all(c.phase == 2 for c in registry.all() if c.id in ids)


async def test_external_checks_reject_phase1_context() -> None:
    from tests.checks.helpers import make_ctx

    checks = [c for c in load_builtin_checks().all() if c.module == "external"]
    result = await run_checks(make_ctx(), checks)
    assert sorted(result.failed) == sorted(EXTERNAL_IDS)


# --- modül uçtan uca (sahte dış dünya)


async def test_module_end_to_end_on_custom_domain() -> None:
    sink = MemoryEventSink()
    with respx.mock(assert_all_called=False) as router:
        mock_world(router)
        ctx = ctx_for(sink=sink)
        out = ModuleOutput()
        await module().run(ctx, out)
    data = ctx.external
    assert data is not None
    assert all(v == PartStatus.OK for v in data.status.values()), data.status
    found = by_id(out)
    subs = {f.evidence["host"]: f for f in found["web.external.forgotten_subdomain"]}
    # Üç farklı yapı: Next.js SPA (.env), WordPress (yalnız HTTP + yedek), API (.git, ana sayfa 404)
    assert set(subs) == {"staging.example.com", "old.example.com", "api.example.com"}
    assert subs["staging.example.com"].severity == Severity.CRITICAL
    old_checks = {c["check_id"] for c in subs["old.example.com"].evidence["checks"]}
    assert old_checks == {"web.files.backup_exposed", "web.https.missing"}
    assert {c["check_id"] for c in subs["api.example.com"].evidence["checks"]} == {
        "web.files.git_exposed"
    }
    # Temiz blog ve sadece noindex olan önizleme bulgu üretmez
    assert "blog.example.com" not in subs and "preview.example.com" not in subs
    takeovers = {
        f.evidence["host"]: f.severity for f in found["web.external.subdomain_takeover_risk"]
    }
    assert takeovers == {
        "gone.example.com": Severity.MEDIUM,
        "docs.example.com": Severity.MEDIUM,
        "legacy.example.com": Severity.HIGH,
    }
    # Arşiv: .env hâlâ açık (high), diğerleri artık yok (info); SPA catch-all yanlış alarm değil
    archived = {f.evidence["state"]: f for f in found["web.external.archived_sensitive_paths"]}
    assert [p["path"] for p in archived["live"].evidence["paths"]] == ["/.env"]
    assert {p["path"] for p in archived["gone"].evidence["paths"]} == {
        "/.git/config",
        "/backup/db_2020.sql",
        "/old/config.json",
        "/swagger.json",
    }
    # İyi e-posta yapılandırması, uzak bitiş tarihleri → bulgu yok
    assert "web.external.email_spoofing" not in found
    assert "web.external.domain_expiry_soon" not in found
    assert "web.external.cert_expiry_soon" not in found
    # İç ağa çözülen ve hiç çözülmeyen adlar taranmaz
    meta = ctx.meta["external"]
    assert "internal.example.com" not in meta["subdomains_scanned"]
    assert "mail.example.com" not in meta["subdomains_scanned"]
    assert meta["domain"] == "example.com" and meta["email"]["dmarc_policy"] == "reject"
    assert "checks_skipped" not in ctx.meta
    keys = [e[1] for e in sink.events]
    assert "deep.event.subdomains_found" in keys and "deep.event.archive_checked" in keys
    assert "deep.event.external_partial" not in keys


async def test_module_never_sends_archived_query_strings() -> None:
    with respx.mock(assert_all_called=False) as router:
        mock_world(router)
        await run_module(module())
        target_calls = [
            c.request.url for c in router.calls if c.request.url.host == "www.example.com"
        ]
    assert target_calls and all(not u.query for u in target_calls)


async def test_shared_hosting_target_only_checks_archive() -> None:
    target = "https://my-app.lovable.app"
    with respx.mock(assert_all_called=False) as router:
        crtsh = router.get(host="crt.sh").mock(return_value=httpx.Response(200, json=[]))
        rdap = router.get(host="rdap.org").mock(return_value=httpx.Response(200, json={}))
        router.get(host="web.archive.org").mock(return_value=httpx.Response(200, json=[]))
        dns = FakeDns({})
        ctx, out = await run_module(module(dns), target)
    assert not crtsh.called and not rdap.called and dns.queries == []
    assert ctx.external is not None and ctx.external.status == {
        "subdomains": PartStatus.SHARED_HOST,
        "email": PartStatus.SHARED_HOST,
        "wayback": PartStatus.OK,
        "rdap": PartStatus.SHARED_HOST,
        "cert": PartStatus.SHARED_HOST,
    }
    assert set(ctx.meta["checks_skipped"]) == EXTERNAL_IDS - {
        "web.external.archived_sensitive_paths"
    }
    assert out.findings == []


@pytest.mark.parametrize(
    ("service_host", "failure"),
    [
        ("crt.sh", httpx.Response(503)),
        ("crt.sh", httpx.ReadTimeout("slow")),
        ("crt.sh", httpx.Response(200, text="<html>not json</html>")),
        ("web.archive.org", httpx.ConnectError("down")),
        ("web.archive.org", httpx.Response(429)),
        ("rdap.org", httpx.ReadTimeout("slow")),
        ("rdap.org", httpx.Response(500)),
    ],
)
async def test_service_failure_skips_only_its_checks(
    service_host: str, failure: httpx.Response | Exception
) -> None:
    part = {"crt.sh": "subdomains", "web.archive.org": "wayback", "rdap.org": "rdap"}[service_host]
    with respx.mock(assert_all_called=False) as router:
        mock_world(router)
        # Aynı desenle eklenen route öncekinin yerine geçer (respx)
        if isinstance(failure, Exception):
            router.get(host=service_host).mock(side_effect=failure)
        else:
            router.get(host=service_host).mock(return_value=failure)
        ctx, _ = await run_module(module())
    assert ctx.external is not None
    expected = (
        PartStatus.RATE_LIMITED
        if getattr(failure, "status_code", 0) == 429
        else (PartStatus.UNAVAILABLE)
    )
    assert ctx.external.status[part] == expected
    others = {k: v for k, v in ctx.external.status.items() if k != part}
    assert all(v == PartStatus.OK for v in others.values())
    from launchcheck.deep.external_data import PART_CHECKS

    assert set(ctx.meta["checks_skipped"]) == set(PART_CHECKS[part])


async def test_crtsh_failure_falls_back_to_certspotter() -> None:
    with respx.mock(assert_all_called=False) as router:
        mock_world(router)
        router.get(host="crt.sh").mock(return_value=httpx.Response(502))
        spotter = router.get(host="api.certspotter.com").mock(
            return_value=httpx.Response(200, json=fixture("certspotter.json"))
        )
        ctx, out = await run_module(module())
    assert spotter.called
    assert "include_subdomains=true" in str(spotter.calls[0].request.url)
    assert ctx.external is not None and ctx.external.status["subdomains"] == PartStatus.OK
    assert ctx.meta["external"]["subdomain_source"] == "certspotter"
    assert ctx.meta["external"]["subdomains_found"] == 2  # staging + api; example.org yabancı
    hosts = {f.evidence["host"] for f in by_id(out)["web.external.forgotten_subdomain"]}
    assert hosts == {"staging.example.com", "api.example.com"}


async def test_rdap_without_data_for_tld_is_no_data() -> None:
    with respx.mock(assert_all_called=False) as router:
        mock_world(router)
        router.get(host="rdap.org").mock(return_value=httpx.Response(404))
        ctx, _ = await run_module(module())
    assert ctx.external is not None and ctx.external.status["rdap"] == PartStatus.NO_DATA
    assert ctx.meta["checks_skipped"] == ["web.external.domain_expiry_soon"]


async def test_dns_failure_skips_email_and_subdomains() -> None:
    dns = site_dns()
    dns.fail = {"example.com"} | {
        h
        for h, _ in select_candidates(
            parse_names(fixture("crtsh.json"), "example.com"),
            "www.example.com",
            "example.com",
            [],
            60,
        )
    }
    with respx.mock(assert_all_called=False) as router:
        mock_world(router)
        ctx, _ = await run_module(module(dns))
    assert ctx.external is not None
    assert ctx.external.status["email"] == PartStatus.UNAVAILABLE
    assert ctx.external.status["subdomains"] == PartStatus.UNAVAILABLE
    assert ctx.external.status["wayback"] == PartStatus.OK


async def test_module_fails_when_nothing_is_reachable() -> None:
    dns = FakeDns({}, fail={"example.com"})

    async def no_cert(host: str) -> None:
        return None

    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=httpx.ConnectError("offline"))
        with pytest.raises(ExternalUnavailableError):
            await run_module(module(dns, cert_probe=no_cert))


async def test_global_rate_limit_is_shared_across_scans() -> None:
    redis = FakeRedis()
    limiter = RedisServiceLimiter(redis, {"crtsh": [2, 60], "certspotter": [10, 3600]})  # type: ignore[arg-type]
    assert [await limiter.allow("crtsh") for _ in range(3)] == [True, True, False]
    assert await limiter.allow("certspotter") is True
    assert await limiter.allow("unlisted") is True
    assert all("svc:" in k for k in redis.counts)
    assert sorted(redis.ttls.values()) == [60, 3600]  # servis başına pencere
    # Redis yoksa engellemez (servisin kendi hatası yine yakalanır)
    assert await RedisServiceLimiter(FakeRedis(fail=True), {"crtsh": [1, 60]}).allow("crtsh")  # type: ignore[arg-type]


async def test_rate_limited_service_is_skipped_without_request() -> None:
    class Deny:
        async def allow(self, service: str) -> bool:
            return service != "crtsh"

    with respx.mock(assert_all_called=False) as router:
        mock_world(router)
        mod = module()
        mod._limiter = Deny()
        ctx, _ = await run_module(mod)
        assert not any(c.request.url.host == "crt.sh" for c in router.calls)
    assert ctx.external is not None and ctx.external.status["subdomains"] == PartStatus.RATE_LIMITED


async def test_budget_timeout_marks_slow_part_and_keeps_others() -> None:
    import asyncio

    async def slow_cert(host: str) -> None:
        await asyncio.sleep(30)

    with respx.mock(assert_all_called=False) as router:
        mock_world(router)
        mod = ExternalModule(
            ext_settings(deep_budget_external_s=12),  # check payı düşülünce ~2 sn
            guard=UrlGuard(FakeResolver()),  # type: ignore[arg-type]
            dns=site_dns(),
            limiter=NoLimit(),
            cert_probe=slow_cert,
            scan_subdomain=lambda h, p: _instant_scan(h, p),
        )
        ctx, _ = await run_module(mod)
    assert ctx.external is not None and ctx.external.status["cert"] == PartStatus.TIMEOUT
    assert ctx.external.status["email"] == PartStatus.OK
    assert "web.external.cert_expiry_soon" in ctx.meta["checks_skipped"]


async def _instant_scan(host: str, priority: bool) -> SubdomainScan:
    return SubdomainScan(host, f"https://{host}/", priority, [])


def test_summary_has_no_personal_data() -> None:
    data = ExternalData("www.example.com", "example.com", False)
    data.email = EmailDns("example.com", True, ["v=spf1 -all"], "-all", 0, "reject", True)
    data.cert = CertInfo(
        "www.example.com",
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 12, 30, tzinfo=UTC),
        "R11",
    )
    s = summary(data)
    assert s["email"] == {"has_mx": True, "spf": True, "spf_all": "-all", "dmarc_policy": "reject"}
    assert s["cert"] == {"expires_at": "2026-12-30", "issuer": "R11"}


@pytest.mark.parametrize("lang", ["tr", "en"])
async def test_external_texts_render_without_placeholders(lang: str) -> None:
    registry = load_builtin_checks()
    data = ExternalData("www.example.com", "example.com", False)
    data.subdomains = [
        SubdomainScan(
            "s.example.com",
            "https://s.example.com/",
            True,
            [SubFinding("web.files.env_exposed", "critical", {"path": "/.env"})],
        )
    ]
    data.takeovers = [
        TakeoverRisk("g.example.com", "g.herokuapp.com", "Heroku", "fingerprint", "edge")
    ]
    data.email = EmailDns("example.com", True, [], None, 0, None, False)
    data.archived = [
        ArchivedPath("/.env", "env", "20210101", True),
        ArchivedPath("/a.sql", "sql", "20200101", False),
    ]
    data.domain_expiry = DomainExpiry("example.com", datetime.now(UTC) + timedelta(days=10))
    data.cert = await cert_probe(-2, 90)("www.example.com")
    ctx = ctx_for()
    ctx.external = data
    checks = [c for c in registry.all() if c.module == "external"]
    result = await run_checks(ctx, checks)
    assert {f.check_id for f in result.findings} == EXTERNAL_IDS
    for finding in result.findings:
        check = registry.get(finding.check_id)
        params = check.text_params(finding.evidence, fragment_fn(finding.check_id, lang))
        text = finding_text(finding.check_id, lang, params)
        for part in (text.title, text.description, *text.fix_steps):
            assert "{" not in part, (finding.check_id, part)


# --- orkestratör + rapor


async def test_orchestrator_runs_external_lane_and_report_hides_skipped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    deep_id = await add_deep(sessionmaker, await add_parent(sessionmaker))
    with respx.mock(assert_all_called=False) as router:
        router.get(host="rdap.org").mock(return_value=httpx.Response(404))
        router.get(host="crt.sh").mock(return_value=httpx.Response(200, json=[]))
        router.get(host="web.archive.org").mock(return_value=httpx.Response(200, json=[]))
        mod = ExternalModule(
            ext_settings(),
            guard=UrlGuard(FakeResolver()),  # type: ignore[arg-type]
            dns=FakeDns(GOOD_EMAIL),
            limiter=NoLimit(),
            cert_probe=cert_probe(60),
        )
        cfg = OrchestratorConfig(settings=Settings(deep_scan_timeout_s=60), modules=[mod])
        await DeepOrchestrator(sessionmaker, cfg).run(deep_id)
    async with sessionmaker() as s:
        scan = await s.get(Scan, deep_id)
        rows = list(
            (await s.scalars(select(FindingRow).where(FindingRow.scan_id == deep_id))).all()
        )
    assert scan is not None and scan.progress["modules"]["external"]["status"] == "done"
    assert scan.meta["external"]["status"]["rdap"] == "no_data"
    passed = {p.check_id for p in _passed(scan, rows, load_builtin_checks(), "tr")}
    assert "web.external.email_spoofing" in passed and "web.external.cert_expiry_soon" in passed
    assert "web.external.domain_expiry_soon" not in passed  # veri yok → kontrol edilmedi
    assert uuid.UUID(str(scan.id)) == deep_id
