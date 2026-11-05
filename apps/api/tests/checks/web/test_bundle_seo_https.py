import pytest

from launchcheck.checks.web.bundle import LocalhostUrls, StagingUrls, is_non_prod_host
from launchcheck.checks.web.https import HttpsMissing, MixedContent
from launchcheck.checks.web.seo import NoindexLeft, RobotsDisallowAll, blocks_everything
from tests.checks.helpers import SITE, make_ctx, resp, run


async def test_localhost_positive() -> None:
    ctx = make_ctx(
        scripts={
            f"{SITE}/a.js": 'const API="http://localhost:3000/api"; const b="http://10.0.2.2:8000"',
            f"{SITE}/b.js": 'fetch("http://127.0.0.1/v1/users")',
        }
    )
    (finding,) = await run(LocalhostUrls, ctx)
    assert finding.evidence["urls"] == [
        "http://localhost:3000/api",
        "http://10.0.2.2:8000",
        "http://127.0.0.1/v1/users",
    ]
    assert finding.evidence["files"] == [f"{SITE}/a.js", f"{SITE}/b.js"]


async def test_localhost_negative_bare_host_ignored() -> None:
    ctx = make_ctx(
        scripts={f"{SITE}/a.js": 'new URL(p, "http://localhost"); "https://api.example.com"'}
    )
    assert await run(LocalhostUrls, ctx) == []


async def test_staging_positive() -> None:
    ctx = make_ctx(
        scripts={
            f"{SITE}/a.js": '"https://staging.api.myapp.com/v1" "https://myapp-dev.herokuapp.com" '
            '"https://api.myapp.test/graphql"'
        }
    )
    (finding,) = await run(StagingUrls, ctx)
    assert finding.evidence["count"] == 3


async def test_staging_negative() -> None:
    ctx = make_ctx(
        scripts={
            f"{SITE}/a.js": '"https://api.myapp.com" "https://developer.mozilla.org/x" '
            '"https://dev.to/post" "https://testimonials.myapp.com"'
        }
    )
    assert await run(StagingUrls, ctx) == []


async def test_staging_ignores_own_host() -> None:
    ctx = make_ctx(
        final_url="https://staging.myapp.com/",
        scripts={"https://staging.myapp.com/a.js": '"https://staging.myapp.com/api"'},
    )
    assert await run(StagingUrls, ctx) == []


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("dev.myapp.com", True),
        ("api-staging.myapp.com", True),
        ("api.staging.myapp.com", True),
        ("app.local", True),
        ("devices.myapp.com", False),
        ("staging.com", False),  # kayıtlı alan adının kendisi
        ("api.myapp.com", False),
    ],
)
def test_is_non_prod_host(host: str, expected: bool) -> None:
    assert is_non_prod_host(host) is expected


async def test_noindex_positive_meta_and_header() -> None:
    ctx = make_ctx(
        html='<html><head><meta name="robots" content="noindex, nofollow"></head></html>',
        headers={"X-Robots-Tag": "noindex"},
    )
    (finding,) = await run(NoindexLeft, ctx)
    assert finding.evidence["sources"] == ["x-robots-tag", "meta[name=robots]"]


async def test_noindex_negative() -> None:
    ctx = make_ctx(html='<html><head><meta name="robots" content="index, follow"></head></html>')
    assert await run(NoindexLeft, ctx) == []


async def test_robots_disallow_all_positive() -> None:
    body = "User-agent: Googlebot\nDisallow: /private\n\nUser-agent: *\nDisallow: /\n"
    ctx = make_ctx(routes={f"{SITE}/robots.txt": resp("r", body=body)})
    assert len(await run(RobotsDisallowAll, ctx)) == 1


@pytest.mark.parametrize(
    "body",
    [
        "User-agent: *\nDisallow: /admin\n",
        "User-agent: *\nDisallow:\n",
        "User-agent: BadBot\nDisallow: /\n",
        "User-agent: *\nAllow: /\nDisallow: /\n",
    ],
)
async def test_robots_disallow_all_negative(body: str) -> None:
    ctx = make_ctx(routes={f"{SITE}/robots.txt": resp("r", body=body)})
    assert await run(RobotsDisallowAll, ctx) == []


async def test_robots_spa_catch_all_negative() -> None:
    assert await run(RobotsDisallowAll, make_ctx(spa=True)) == []


def test_blocks_everything_multi_agent_group() -> None:
    assert blocks_everything("User-agent: a\nUser-agent: *\nDisallow: /")


async def test_https_missing_http_site() -> None:
    (finding,) = await run(HttpsMissing, make_ctx(final_url="http://app.example.com/"))
    assert finding.evidence["reason"] == "no_https"


async def test_https_missing_no_redirect() -> None:
    ctx = make_ctx(routes={"http://app.example.com/": resp("h", 200, "<html>", "text/html")})
    (finding,) = await run(HttpsMissing, ctx)
    assert finding.evidence == {"reason": "no_redirect", "http_status": 200}


async def test_https_negative_redirects_to_https() -> None:
    ctx = make_ctx(
        routes={"http://app.example.com/": resp("h", 301, headers={"location": f"{SITE}/"})}
    )
    assert await run(HttpsMissing, ctx) == []


async def test_mixed_content_positive() -> None:
    html = (
        '<html><head><script src="http://cdn.x.com/a.js"></script>'
        '<link rel="stylesheet" href="http://cdn.x.com/s.css"><link rel="icon" href="http://x/i.png">'
        "</head></html>"
    )
    (finding,) = await run(MixedContent, make_ctx(html=html))
    assert finding.evidence["urls"] == ["http://cdn.x.com/a.js", "http://cdn.x.com/s.css"]


async def test_mixed_content_negative() -> None:
    html = '<html><head><script src="https://cdn.x.com/a.js"></script><script src="/b.js"></script></head></html>'
    assert await run(MixedContent, make_ctx(html=html)) == []
    insecure = '<script src="http://cdn.x.com/a.js"></script>'
    assert (
        await run(MixedContent, make_ctx(html=insecure, final_url="http://app.example.com/")) == []
    )


async def test_localhost_library_defaults_ignored() -> None:
    """OpenTelemetry OTLP exporter varsayılanı uygulama hatası değildir."""
    ctx = make_ctx(
        scripts={f"{SITE}/a.js": '"http://localhost:4318/v1/traces" "http://localhost:4317"'}
    )
    assert await run(LocalhostUrls, ctx) == []
