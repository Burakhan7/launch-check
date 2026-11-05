import json

from launchcheck.checks.base import Severity
from launchcheck.checks.web.secrets import GoogleApiKey, LeakedKey, ServiceRole
from tests.checks.helpers import ALNUM, SITE, fake_supabase_jwt, fake_token, make_ctx, run

OPENAI = fake_token("sk-proj-", 48)
STRIPE_TEST = fake_token("sk_" + "test_", 24)
GOOGLE = fake_token("AI" + "za", 35, ALNUM + "_-")


async def test_leaked_key_positive_with_severity_per_pattern() -> None:
    ctx = make_ctx(
        scripts={
            f"{SITE}/assets/a.js": f'fetch(u,{{headers:{{Authorization:"Bearer {OPENAI}"}}}})'
        },
        inline=[f'window.STRIPE="{STRIPE_TEST}"'],
    )
    findings = await run(LeakedKey, ctx)
    by_kind = {f.evidence["kind"]: f for f in findings}
    assert set(by_kind) == {"openai", "stripe_test_secret"}
    assert by_kind["openai"].severity == Severity.CRITICAL
    assert by_kind["stripe_test_secret"].severity == Severity.MEDIUM
    assert by_kind["openai"].evidence["locations"] == [{"file": f"{SITE}/assets/a.js", "line": 1}]
    assert OPENAI not in json.dumps([f.evidence for f in findings])


async def test_leaked_key_negative() -> None:
    ctx = make_ctx(
        scripts={f"{SITE}/a.js": 'const apiKey = import.meta.env.VITE_PUBLIC_URL; "sk-xxxx"'}
    )
    assert await run(LeakedKey, ctx) == []


async def test_google_key_reported_separately() -> None:
    ctx = make_ctx(scripts={f"{SITE}/a.js": f'initializeApp({{apiKey:"{GOOGLE}"}})'})
    assert await run(LeakedKey, ctx) == []
    (finding,) = await run(GoogleApiKey, ctx)
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence["kind"] == "google_api_key"


async def test_google_key_negative() -> None:
    assert await run(GoogleApiKey, make_ctx(scripts={f"{SITE}/a.js": "x"})) == []


async def test_service_role_positive() -> None:
    token = fake_supabase_jwt("service_role")
    ctx = make_ctx(scripts={f"{SITE}/a.js": f'createClient("https://x.supabase.co", "{token}")'})
    (finding,) = await run(ServiceRole, ctx)
    assert finding.severity == Severity.CRITICAL
    assert finding.evidence["project_ref"] == "abcdefghijklmnopqrst"
    assert token not in json.dumps(finding.evidence)


async def test_service_role_negative_anon_key_is_fine() -> None:
    ctx = make_ctx(scripts={f"{SITE}/a.js": f'createClient(u, "{fake_supabase_jwt("anon")}")'})
    assert await run(ServiceRole, ctx) == []
    assert await run(LeakedKey, ctx) == []
