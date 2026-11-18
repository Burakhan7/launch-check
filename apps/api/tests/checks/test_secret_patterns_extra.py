"""Ek Paket 1 secret desenleri: basit + bağlamlı desenler ve DB bağlantı adresleri.

Gerçeğe benzeyen değerler repoya düz metin olarak girmez; test anında birleştirilerek üretilir.
"""

import json
import string
from pathlib import Path

import pytest

from launchcheck.checks.apk.secrets import DbConnectionString as ApkDbConnectionString
from launchcheck.checks.apk.secrets import LeakedKey as ApkLeakedKey
from launchcheck.checks.base import Severity
from launchcheck.checks.shared.secret_patterns import (
    connection_evidence,
    find_connection_strings,
    find_secrets,
    to_evidence,
)
from launchcheck.checks.web.secrets import DbConnectionString, LeakedKey
from launchcheck.i18n import finding_text, fragment_fn
from tests.checks.apk_helpers import make_apk_ctx
from tests.checks.apk_helpers import run as run_apk
from tests.checks.helpers import ALNUM, HEX, fake_token, make_ctx, run

DIGITS = string.digits
UPPER_DIGITS = string.ascii_uppercase + DIGITS


def slack_webhook() -> str:
    return (
        "https://hooks.slack.com/services/"
        + fake_token("T", 8, UPPER_DIGITS)
        + "/"
        + fake_token("B", 10, UPPER_DIGITS)
        + "/"
        + fake_token("", 24, ALNUM)
    )


def discord_webhook() -> str:
    return (
        "https://discord.com/api/"
        + "webhooks/"
        + fake_token("1", 17, DIGITS)
        + "/"
        + fake_token("", 68, ALNUM + "_-")
    )


SIMPLE = [
    ("huggingface", fake_token("hf" + "_", 34), Severity.HIGH),
    (
        "mapbox_secret",
        "sk" + ".eyJ" + fake_token("", 60) + "." + fake_token("", 22, ALNUM + "_-"),
        Severity.HIGH,
    ),
    ("slack_token", fake_token("xox" + "b-", 40, ALNUM + "-"), Severity.HIGH),
    ("slack_webhook", slack_webhook(), Severity.HIGH),
    ("discord_webhook", discord_webhook(), Severity.HIGH),
    (
        "telegram_bot_token",
        fake_token("7", 8, DIGITS) + ":" + fake_token("AA", 33, ALNUM + "_-"),
        Severity.HIGH,
    ),
    ("mailgun", fake_token("key" + "-", 32, HEX), Severity.HIGH),
    (
        "cloudinary_url",
        "cloudinary" + "://" + fake_token("8", 14, DIGITS) + ":" + fake_token("", 27) + "@mycloud",
        Severity.HIGH,
    ),
    ("npm_token", fake_token("npm" + "_", 36), Severity.CRITICAL),
]


@pytest.mark.parametrize(("pattern_id", "secret", "severity"), SIMPLE)
def test_new_pattern_detected(pattern_id: str, secret: str, severity: Severity) -> None:
    hits = find_secrets([("app.js", f'const c = {{ key: "{secret}" }};')])
    assert [(h.pattern_id, h.severity, h.value) for h in hits] == [(pattern_id, severity, secret)]
    evidence = json.dumps(to_evidence(hits[0]))
    assert secret not in evidence


@pytest.mark.parametrize(
    "text",
    [
        "hf" + "_" + "x" * 34,
        "sk" + ".eyJ" + "x" * 40 + ".xxxxxxxx",
        "xox" + "b-your-slack-token-here",
        "https://hooks.slack.com/services/T00000000/B00000000/" + "X" * 24,
        "https://discord.com/api/" + "webhooks/" + "0" * 18 + "/" + "x" * 68,
        "123456789" + ":AA" + "x" * 33,
        "key" + "-" + "0" * 32,
        "cloudinary" + "://123456789012345:your_api_secret_here_abc@demo",
        "npm" + "_" + "x" * 36,
    ],
)
def test_new_pattern_placeholders_ignored(text: str) -> None:
    assert find_secrets([("a.js", f'k = "{text}"')]) == []


@pytest.mark.parametrize(("pattern_id", "secret", "_severity"), SIMPLE)
def test_new_pattern_requires_word_boundary(
    pattern_id: str, secret: str, _severity: Severity
) -> None:
    """Daha uzun bir kelimenin parçası olduğunda eşleşmemeli."""
    hits = find_secrets([("a.js", f'k = "z{secret}"')])
    assert pattern_id not in {h.pattern_id for h in hits}


def test_npm_token_exact_length() -> None:
    assert find_secrets([("a.js", fake_token("npm" + "_", 37))]) == []


def test_cloudinary_evidence_never_contains_api_secret() -> None:
    api_secret = fake_token("", 27, seed=5)
    url = "cloudinary" + "://" + fake_token("8", 14, DIGITS) + ":" + api_secret + "@mycloud"
    (hit,) = find_secrets([("a.js", f'"{url}"')])
    masked = to_evidence(hit)["masked"]
    assert api_secret not in masked and api_secret[-4:] not in masked
    assert masked.startswith("cloudinary://") and masked.endswith("@mycloud")


# --- bağlamlı desenler (iyzico, PayTR)

IYZI_VALUE = fake_token("", 32, seed=11)
PAYTR_VALUE = fake_token("", 16, seed=12)


def test_iyzico_with_context() -> None:
    js = f'const c = new Iyzipay({{ apiKey: "a", secretKey: "{IYZI_VALUE}" }});'
    (hit,) = find_secrets([("a.js", js)])
    assert (hit.pattern_id, hit.severity, hit.value) == (
        "iyzico_secret",
        Severity.CRITICAL,
        IYZI_VALUE,
    )


def test_iyzico_sandbox_is_medium() -> None:
    value = "sandbox-" + IYZI_VALUE
    js = f'iyzico.config = {{ "secretKey": "{value}" }}'
    (hit,) = find_secrets([("a.js", js)])
    assert hit.severity == Severity.MEDIUM and hit.value == value


def test_iyzico_without_context_not_matched() -> None:
    assert find_secrets([("a.js", f'cfg = {{ secretKey: "{IYZI_VALUE}" }}')]) == []


def test_context_must_be_within_window() -> None:
    far = "iyzipay" + " " * 3000 + f'secretKey: "{IYZI_VALUE}"'
    assert find_secrets([("a.js", far)]) == []
    # Bağlam başka bir dosyada ise sayılmaz
    sources = [("a.js", "import Iyzipay from 'iyzipay'"), ("b.js", f'secretKey: "{IYZI_VALUE}"')]
    assert find_secrets(sources) == []


def test_iyzico_placeholder_ignored() -> None:
    js = 'iyzipay({ secretKey: "sandbox-xxxxxxxxxxxxxxxxxxxxxxxx" })'
    assert find_secrets([("a.js", js)]) == []


def test_paytr_with_and_without_context() -> None:
    js = f'const paytr_merchant_salt = "{PAYTR_VALUE}";'
    (hit,) = find_secrets([("a.js", js)])
    assert (hit.pattern_id, hit.severity) == ("paytr_merchant_secret", Severity.CRITICAL)
    assert find_secrets([("a.js", f'merchant_key: "{PAYTR_VALUE}"')]) == []


def test_paytr_partial_word_not_matched() -> None:
    js = f'PayTR; const not_merchant_keyx = "{PAYTR_VALUE}";'
    assert find_secrets([("a.js", js)]) == []


@pytest.mark.parametrize("lang", ["tr", "en"])
async def test_payment_kind_adds_payment_fix_step(lang: str) -> None:
    js = f'new Iyzipay({{ secretKey: "{IYZI_VALUE}" }})'
    (finding,) = await run(LeakedKey, make_ctx(scripts={"https://app.example.com/a.js": js}))
    check = LeakedKey()
    text = finding_text(
        check.id, lang, check.text_params(finding.evidence, fragment_fn(check.id, lang))
    )
    plain = finding_text(
        check.id, lang, check.text_params({"kind": "openai"}, fragment_fn(check.id, lang))
    )
    assert len(text.fix_steps) == len(plain.fix_steps) + 1
    assert "Clerk" in text.description
    assert IYZI_VALUE not in json.dumps(finding.evidence)


# --- DB bağlantı adresleri


def pw(seed: int = 1, n: int = 24) -> str:
    return fake_token("", n, seed=seed)


def test_supabase_pooler_connection_is_critical() -> None:
    password = pw()
    url = (
        "postgres"
        + "://postgres.abcd:"
        + password
        + "@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
    )
    scan = find_connection_strings([("/assets/index-abc.js", f'const db = "{url}";')])
    (hit,) = scan.hits
    assert hit.severity == Severity.CRITICAL
    evidence = connection_evidence(hit)
    assert evidence == {
        "kind": "postgres",
        "provider_domain": "supabase.com",
        "password_length": 24,
        "locations": [{"file": "/assets/index-abc.js", "line": 1}],
        "location_count": 1,
    }
    dumped = json.dumps(evidence)
    for leaked in (password, "postgres.abcd", "aws-0-eu-central-1", "pooler.supabase.com"):
        assert leaked not in dumped


@pytest.mark.parametrize(
    ("url", "kind", "provider", "severity"),
    [
        (
            f"postgresql://u1:{pw(2)}@mydb.abc.us-east-1.rds.amazonaws.com:5432/x",
            "postgres",
            "rds.amazonaws.com",
            Severity.CRITICAL,
        ),
        (
            f"mongodb+srv://app:{pw(3)}@cluster0.ab1cd.mongodb.net/db",
            "mongodb",
            "mongodb.net",
            Severity.CRITICAL,
        ),
        (
            f"rediss://default:{pw(4)}@eu1-fun-cat.upstash.io:6379",
            "redis",
            "upstash.io",
            Severity.CRITICAL,
        ),
        (f"mysql://admin:{pw(5)}@db.mycompany.io/app", "mysql", "mycompany.io", Severity.HIGH),
        (
            f"amqps://worker:{pw(6)}@rabbit.mycompany.com.tr/vh",
            "amqp",
            "mycompany.com.tr",
            Severity.HIGH,
        ),
    ],
)
def test_connection_kinds_and_providers(
    url: str, kind: str, provider: str, severity: Severity
) -> None:
    (hit,) = find_connection_strings([("a.js", f"'{url}'")]).hits
    assert (hit.kind, hit.provider_domain, hit.severity) == (kind, provider, severity)


@pytest.mark.parametrize(
    "url",
    [
        "postgres://user:password@localhost/db",
        "mongodb+srv://${USER}:${PASS}@cluster0.x.mongodb.net",
        "postgres://u:p@h/db",  # test_files.py ENV_BODY örneği
        "postgres://app:{{password}}@db.supabase.co/x",
        "mysql://root:<password>@db.mycompany.io/app",
        "redis://admin123:admin123@redis.upstash.io",  # kullanıcı = şifre
        "postgres://app:abc12@db.mycompany.io/x",  # 6 karakterden kısa
        "postgres://app:%s@db.mycompany.io/x",
        "postgres://app:changeme@db.mycompany.io/x",
        "postgres://app:your-db-password@db.mycompany.io/x",
    ],
)
def test_connection_placeholders_ignored(url: str) -> None:
    scan = find_connection_strings([("a.js", f'"{url}"')])
    assert scan.hits == []


@pytest.mark.parametrize(
    "host",
    [
        "localhost:5432",
        "127.0.0.1",
        "host.docker.internal",
        "db",
        "postgres",
        "pg.local",
        "db.internal",
        "x.test",
        "db.example.com",
    ],
)
def test_local_hosts_counted_not_reported(host: str) -> None:
    scan = find_connection_strings([("a.js", f'"postgres://app:{pw(7)}@{host}/db"')])
    assert scan.hits == [] and scan.local_count == 1


def test_connection_boundary_and_dedup() -> None:
    url = f"postgres://app:{pw(8)}@db.mycompany.io/x"
    scan = find_connection_strings(
        [("a.js", f'"x{url}"'), ("b.js", f'"{url}"'), ("c.js", f"'{url}'\n'{url}'")]
    )
    (hit,) = scan.hits
    assert [(loc.file, loc.line) for loc in hit.locations] == [
        ("b.js", 1),
        ("c.js", 1),
        ("c.js", 2),
    ]


async def test_web_db_connection_check_and_meta() -> None:
    password = pw(9)
    js = (
        f'a="postgres://postgres.ref:{password}@aws-0-eu.pooler.supabase.com:5432/postgres";'
        f'b="postgres://app:{pw(10)}@localhost:5432/dev";'
    )
    ctx = make_ctx(scripts={"https://app.example.com/assets/index.js": js})
    (finding,) = await run(DbConnectionString, ctx)
    assert finding.severity == Severity.CRITICAL
    assert ctx.meta["local_db_strings"] == 1
    assert password not in json.dumps(finding.evidence)
    check = DbConnectionString()
    text = finding_text(
        check.id, "tr", check.text_params(finding.evidence, fragment_fn(check.id, "tr"))
    )
    assert "postgres, supabase.com" in text.description


async def test_web_db_connection_negative() -> None:
    ctx = make_ctx(
        scripts={"https://app.example.com/a.js": 'x="postgres://user:password@localhost/db"'}
    )
    assert await run(DbConnectionString, ctx) == []
    assert "local_db_strings" not in ctx.meta  # yer tutucu şifre yerel sayaca da girmez


async def test_apk_db_connection_check(tmp_path: Path) -> None:
    url = f"mongodb+srv://app:{pw(11)}@cluster0.ab1cd.mongodb.net/db"
    ctx = make_apk_ctx(tmp_path, sources=[("assets/.env", f"DATABASE_URL={url}\n")])
    (finding,) = await run_apk(ApkDbConnectionString, ctx)
    assert finding.evidence["provider_domain"] == "mongodb.net"
    assert finding.evidence["locations"] == [{"file": "assets/.env", "line": 1}]
    # Bağlantı adresi leaked_key olarak ayrıca raporlanmaz
    assert await run_apk(ApkLeakedKey, ctx) == []


async def test_apk_db_connection_negative(tmp_path: Path) -> None:
    ctx = make_apk_ctx(
        tmp_path, sources=[("smali/A.smali", 'const-string v0, "postgres://u:p@h/db"')]
    )
    assert await run_apk(ApkDbConnectionString, ctx) == []
