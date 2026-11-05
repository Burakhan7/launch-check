import json

import pytest

from launchcheck.checks.base import Severity
from launchcheck.checks.shared.secret_patterns import (
    decode_jwt_payload,
    find_secrets,
    find_supabase_jwts,
    is_placeholder,
    shannon_entropy,
    to_evidence,
)
from tests.checks.helpers import ALNUM, HEX, fake_supabase_jwt, fake_token

CASES = [
    ("openai", fake_token("sk-proj-", 48, ALNUM + "_-"), Severity.CRITICAL),
    ("openai", fake_token("sk-", 48), Severity.CRITICAL),
    ("anthropic", fake_token("sk-ant-api03-", 93, ALNUM + "_-"), Severity.CRITICAL),
    ("openrouter", fake_token("sk-or-v1-", 64, HEX), Severity.CRITICAL),
    ("groq", fake_token("gsk_", 52), Severity.CRITICAL),
    ("stripe_live_secret", fake_token("sk_" + "live_", 24), Severity.CRITICAL),
    ("stripe_live_secret", fake_token("rk_" + "live_", 24), Severity.CRITICAL),
    ("stripe_test_secret", fake_token("sk_" + "test_", 24), Severity.MEDIUM),
    ("supabase_secret", fake_token("sb_" + "secret_", 32), Severity.CRITICAL),
    (
        "aws_access_key",
        fake_token("AK" + "IA", 16, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"),
        Severity.CRITICAL,
    ),
    ("github_token", fake_token("gh" + "p_", 36), Severity.CRITICAL),
    (
        "sendgrid",
        fake_token("SG.", 22, ALNUM + "_-") + "." + fake_token("", 43, ALNUM + "_-"),
        Severity.HIGH,
    ),
    ("resend", fake_token("re_", 10) + "_" + fake_token("", 24, ALNUM, seed=2), Severity.HIGH),
    ("twilio", fake_token("SK", 32, HEX), Severity.HIGH),
    ("replicate", fake_token("r8_", 37), Severity.HIGH),
    ("private_key", "-----BEGIN RSA PRIVATE KEY-----", Severity.CRITICAL),
    ("google_api_key", fake_token("AI" + "za", 35, ALNUM + "_-"), Severity.MEDIUM),
]


@pytest.mark.parametrize(("pattern_id", "secret", "severity"), CASES)
def test_each_pattern_detected(pattern_id: str, secret: str, severity: Severity) -> None:
    js = f'const config = {{ key: "{secret}" }};'
    hits = find_secrets([("app.js", js)])
    assert [(h.pattern_id, h.severity, h.value) for h in hits] == [(pattern_id, severity, secret)]


@pytest.mark.parametrize(
    "text",
    [
        'apiKey: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"',
        'apiKey: "sk-your-api-key-here-please-change"',
        'OPENAI_API_KEY="sk-proj-REPLACE_WITH_YOUR_OWN_KEY_123"',
        'stripe: "sk_' + 'test_1234567890abcdefghijklmn"',
        'key: "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
        'cls: "sk-modal-overlay-container-wrapper"',  # düşük entropili CSS sınıfı
        "AKIAIOSFODNN7EXAMPLE",  # AWS dokümantasyon örneği
        "const ask_test_value = 1; // sk_test_ ile başlamıyor",
    ],
)
def test_placeholders_and_low_entropy_ignored(text: str) -> None:
    assert find_secrets([("a.js", text)]) == []


def test_word_boundary_required() -> None:
    secret = fake_token("gh" + "p_", 36)
    assert find_secrets([("a.js", f"x{secret}")]) == []


def test_more_specific_pattern_wins_over_generic_openai() -> None:
    secret = fake_token("sk-ant-api03-", 93, ALNUM + "_-")
    hits = find_secrets([("a.js", secret)])
    assert [h.pattern_id for h in hits] == ["anthropic"]


def test_same_secret_in_many_files_is_one_hit_with_locations() -> None:
    secret = fake_token("sk-proj-", 48)
    hits = find_secrets(
        [("a.js", f"\n\nx='{secret}'"), ("b.js", f"y='{secret}'"), ("a.js", f"z='{secret}'")]
    )
    assert len(hits) == 1
    assert [(loc.file, loc.line) for loc in hits[0].locations] == [
        ("a.js", 3),
        ("b.js", 1),
        ("a.js", 1),
    ]


def test_evidence_is_masked_and_never_contains_full_secret() -> None:
    secret = fake_token("sk-proj-", 48)
    (hit,) = find_secrets([("a.js", secret)])
    evidence = to_evidence(hit)
    assert evidence["masked"] == secret[:6] + "…" + secret[-4:]
    assert secret not in json.dumps(evidence)
    assert evidence["locations"] == [{"file": "a.js", "line": 1}]


def test_entropy_and_placeholder_helpers() -> None:
    assert shannon_entropy("aaaa") == 0
    assert shannon_entropy(fake_token("", 40)) > 4
    assert is_placeholder("sk-your-key")
    assert is_placeholder("abc0000000000def")


def test_supabase_jwt_roles() -> None:
    service = fake_supabase_jwt("service_role")
    anon = fake_supabase_jwt("anon")
    hits = {h.extra["role"]: h for h in find_supabase_jwts([("a.js", f"'{service}' '{anon}'")])}
    assert hits["service_role"].severity == Severity.CRITICAL
    assert hits["anon"].severity == Severity.INFO
    assert to_evidence(hits["service_role"])["project_ref"] == "abcdefghijklmnopqrst"


def test_non_supabase_jwt_ignored() -> None:
    import base64

    payload = base64.urlsafe_b64encode(b'{"iss":"auth0","role":"admin"}').decode().rstrip("=")
    token = f"eyJhbGciOiJIUzI1NiJ9.{payload}.c2lnbmF0dXJl"
    assert find_supabase_jwts([("a.js", token)]) == []


def test_decode_jwt_payload_bad_input() -> None:
    assert decode_jwt_payload("eyJ.eyJ!!!.x") is None
    assert decode_jwt_payload("nodots") is None
