import pytest

from launchcheck.checks.shared.masking import mask_secret
from launchcheck.security.url import TargetValidationError, normalize_target_url


def test_mask_long_secret() -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz1234"
    masked = mask_secret(secret)
    assert masked == "sk-pro…1234"
    assert "abcdefghijklmnop" not in masked


def test_mask_short_secret_reveals_little() -> None:
    assert mask_secret("abc123def456") == "ab…"


@pytest.mark.parametrize(
    ("raw", "url", "origin"),
    [
        ("example.com", "https://example.com/", "https://example.com"),
        ("HTTPS://Example.COM/app?token=x#y", "https://example.com/app", "https://example.com"),
        ("http://example.com:80/", "http://example.com/", "http://example.com"),
        ("http://example.com:443", "http://example.com:443/", "http://example.com:443"),
        ("https://bücher.de", "https://xn--bcher-kva.de/", "https://xn--bcher-kva.de"),
        ("https://[::1]/", "https://[::1]/", "https://[::1]"),
    ],
)
def test_normalize_ok(raw: str, url: str, origin: str) -> None:
    t = normalize_target_url(raw)
    assert (t.url, t.origin) == (url, origin)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("", "invalid_url"),
        ("   ", "invalid_url"),
        ("ftp://example.com", "url_scheme_not_allowed"),
        ("javascript:alert(1)", "invalid_url"),
        ("file:///etc/passwd", "url_scheme_not_allowed"),
        ("https://example.com:8080", "url_port_not_allowed"),
        ("https://user:pass@example.com", "invalid_url"),
        ("https://exa mple.com", "invalid_url"),
        ("https://example.com:99999", "invalid_url"),
        ("https://", "invalid_url"),
    ],
)
def test_normalize_rejects(raw: str, code: str) -> None:
    with pytest.raises(TargetValidationError) as exc:
        normalize_target_url(raw)
    assert exc.value.code == code
