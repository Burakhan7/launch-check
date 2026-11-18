"""web.files.api_docs_exposed ve web.files.dev_files_exposed."""

import json

import pytest

from launchcheck.checks.base import Severity
from launchcheck.checks.web.files import DEV_FILES, ApiDocsExposed, DevFilesExposed
from launchcheck.config import get_settings
from launchcheck.i18n import finding_text, fragment_fn
from tests.checks.helpers import SITE, fake_token, make_ctx, resp, run

SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Shop API", "version": "1"},
    "servers": [{"url": "https://internal-api.shop.example.com"}],
    "paths": {
        "/items": {"get": {"parameters": [{"name": "q", "example": "gizli-ornek"}]}},
        "/admin/users": {"get": {}, "delete": {}},
        "/internal/export": {"post": {}},
    },
}
SWAGGER_HTML = (
    "<!doctype html><html><head><link rel=stylesheet href=swagger-ui.css></head><body>"
    "<div id=swagger-ui></div><script>SwaggerUIBundle({url: '/openapi.json', "
    "dom_id: '#swagger-ui'})</script></body></html>"
)


def json_resp(path: str, data: object) -> object:
    return resp(f"{SITE}{path}", body=json.dumps(data), content_type="application/json")


# --- api_docs_exposed


async def test_api_spec_without_auth_and_sensitive_paths_is_high() -> None:
    ctx = make_ctx(
        routes={
            f"{SITE}/openapi.json": json_resp("/openapi.json", SPEC),  # type: ignore[dict-item]
            f"{SITE}/docs": resp(f"{SITE}/docs", body=SWAGGER_HTML, content_type="text/html"),
        }
    )
    (finding,) = await run(ApiDocsExposed, ctx)
    assert finding.severity == Severity.HIGH
    assert finding.evidence == {
        "path": "/openapi.json",
        "doc_ui": "/docs",
        "endpoint_count": 4,
        "sensitive_paths": ["/admin/users", "/internal/export"],
        "has_security_schemes": False,
    }
    dumped = json.dumps(finding.evidence)
    assert "gizli-ornek" not in dumped and "internal-api" not in dumped  # örnekler/servers yok


async def test_api_spec_with_security_schemes_is_medium() -> None:
    spec = {**SPEC, "components": {"securitySchemes": {"bearer": {"type": "http"}}}}
    ctx = make_ctx(routes={f"{SITE}/swagger.json": json_resp("/swagger.json", spec)})  # type: ignore[dict-item]
    (finding,) = await run(ApiDocsExposed, ctx)
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence["has_security_schemes"] is True


async def test_doc_ui_follows_linked_spec_once() -> None:
    html = SWAGGER_HTML.replace("/openapi.json", "/api/v1/spec.json")
    ctx = make_ctx(
        routes={
            f"{SITE}/docs": resp(f"{SITE}/docs", body=html, content_type="text/html"),
            f"{SITE}/api/v1/spec.json": json_resp("/api/v1/spec.json", SPEC),  # type: ignore[dict-item]
        }
    )
    (finding,) = await run(ApiDocsExposed, ctx)
    assert finding.evidence["path"] == "/api/v1/spec.json"
    assert finding.evidence["doc_ui"] == "/docs"


async def test_truncated_spec_detected_by_signature() -> None:
    body = b'{"openapi":"3.0.0","info":{},"paths":{"/a":' + b" " * 2000
    res = resp(f"{SITE}/openapi.json", body=body, content_type="application/json")
    object.__setattr__(res, "truncated", True)
    (finding,) = await run(ApiDocsExposed, make_ctx(routes={f"{SITE}/openapi.json": res}))
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence == {"path": "/openapi.json"}


async def test_marketing_docs_page_is_not_api_docs() -> None:
    html = "<html><body><h1>Docs</h1><p>Getting started with our product.</p></body></html>"
    ctx = make_ctx(
        routes={f"{SITE}/docs": resp(f"{SITE}/docs", body=html, content_type="text/html")}
    )
    assert await run(ApiDocsExposed, ctx) == []


async def test_json_without_paths_is_not_a_spec() -> None:
    ctx = make_ctx(routes={f"{SITE}/openapi.json": json_resp("/openapi.json", {"openapi": "3"})})  # type: ignore[dict-item]
    assert await run(ApiDocsExposed, ctx) == []


@pytest.mark.parametrize("spa", [True, False])
async def test_api_docs_spa_and_404_negative(spa: bool) -> None:
    assert await run(ApiDocsExposed, make_ctx(spa=spa)) == []


async def test_api_docs_same_as_baseline_negative() -> None:
    ctx = make_ctx()
    same = json_resp("/x", SPEC)
    for path in get_settings().web_api_doc_paths:
        ctx.fetcher.routes[SITE + path] = same  # type: ignore[union-attr, assignment]
    ctx.fetcher.routes[SITE + ctx._baseline_path] = same  # type: ignore[union-attr, assignment]
    assert await run(ApiDocsExposed, ctx) == []


def test_api_docs_text_without_endpoint_count() -> None:
    check = ApiDocsExposed()
    for lang in ("tr", "en"):
        params = check.text_params({"path": "/docs"}, fragment_fn(check.id, lang))
        text = finding_text(check.id, lang, params)
        assert "/docs" in text.description and "{" not in text.description


# --- dev_files_exposed

NPM_TOKEN = fake_token("npm_", 36)
COMPOSE = (
    "services:\n  web:\n    image: app\n    environment:\n"
    "      - DB_PASSWORD=supersecret123\n  db:\n    image: postgres\n"
    "    environment:\n      POSTGRES_PASSWORD: hunter2hunter2\n"
)


def positive_routes() -> dict[str, object]:
    return {
        f"{SITE}/.npmrc": resp("n", body=f"//registry.npmjs.org/:_authToken={NPM_TOKEN}\n"),
        f"{SITE}/phpinfo.php": resp(
            "p",
            body="<html><title>phpinfo()</title><h1>PHP Version 8.2</h1>",
            content_type="text/html",
        ),
        f"{SITE}/docker-compose.yml": resp("d", body=COMPOSE, content_type="application/yaml"),
        f"{SITE}/.DS_Store": resp(
            "s", body=b"\x00\x00\x00\x01Bud1\x00\x00", content_type="application/octet-stream"
        ),
        f"{SITE}/.vscode/settings.json": resp("v", body='{"editor.tabSize": 2}'),
        f"{SITE}/.idea/workspace.xml": resp(
            "i", body='<?xml version="1.0"?><project version="4"/>'
        ),
        f"{SITE}/.firebaserc": resp("f", body='{"projects": {"default": "my-proj"}}'),
    }


async def test_dev_files_positive_all() -> None:
    ctx = make_ctx(routes=positive_routes())  # type: ignore[arg-type]
    (finding,) = await run(DevFilesExposed, ctx)
    assert finding.severity == Severity.CRITICAL
    paths = [f["path"] for f in finding.evidence["files"]]
    assert paths == [
        "/.npmrc",
        "/phpinfo.php",
        "/docker-compose.yml",
        "/.DS_Store",
        "/.vscode/settings.json",
        "/.idea/workspace.xml",
        "/.firebaserc",
    ]
    files = {f["path"]: f for f in finding.evidence["files"]}
    assert files["/.npmrc"] == {"path": "/.npmrc", "has_auth_token": True}
    assert files["/docker-compose.yml"]["services"] == ["web", "db"]
    dumped = json.dumps(finding.evidence)
    for secret in (NPM_TOKEN, "supersecret123", "hunter2hunter2", "DB_PASSWORD", "my-proj"):
        assert secret not in dumped


async def test_dev_files_severity_is_highest_file() -> None:
    routes = {f"{SITE}/.DS_Store": positive_routes()[f"{SITE}/.DS_Store"]}
    (finding,) = await run(DevFilesExposed, make_ctx(routes=routes))  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW


@pytest.mark.parametrize("spa", [True, False])
async def test_dev_files_spa_and_404_negative(spa: bool) -> None:
    assert await run(DevFilesExposed, make_ctx(spa=spa)) == []


async def test_dev_files_same_as_baseline_negative() -> None:
    ctx = make_ctx()
    same = resp("x", body="services:\n  web: {}\n")
    for path in DEV_FILES:
        ctx.fetcher.routes[SITE + path] = same  # type: ignore[union-attr]
    ctx.fetcher.routes[SITE + ctx._baseline_path] = same  # type: ignore[union-attr]
    assert await run(DevFilesExposed, ctx) == []


@pytest.mark.parametrize(
    ("path", "body", "ctype"),
    [
        ("/docker-compose.yml", "version: '3'\nnetworks: {}\n", "text/plain"),  # services: yok
        ("/.npmrc", "registry=https://registry.npmjs.org/\n", "text/plain"),  # token yok
        ("/phpinfo.php", "<?php phpinfo(); ?>", "text/plain"),  # çalışmayan PHP (HTML değil)
        ("/.DS_Store", "hello", "application/octet-stream"),
        ("/.vscode/settings.json", "not json", "application/json"),
        ("/.idea/workspace.xml", "<project", "application/xml"),
        ("/.firebaserc", '{"targets": {}}', "application/json"),
    ],
)
async def test_dev_files_wrong_content_negative(path: str, body: str, ctype: str) -> None:
    ctx = make_ctx(routes={SITE + path: resp(SITE + path, body=body, content_type=ctype)})
    assert await run(DevFilesExposed, ctx) == []


def test_dev_files_npm_step_only_when_npmrc_found() -> None:
    check = DevFilesExposed()
    for lang in ("tr", "en"):
        fragment = fragment_fn(check.id, lang)
        with_npm = finding_text(
            check.id,
            lang,
            check.text_params({"files": [{"path": "/.npmrc", "has_auth_token": True}]}, fragment),
        )
        without = finding_text(
            check.id, lang, check.text_params({"files": [{"path": "/.DS_Store"}]}, fragment)
        )
        assert "npmjs.com" in with_npm.fix_steps[0]
        assert len(without.fix_steps) == len(with_npm.fix_steps) - 1
        assert all("{" not in s for s in without.fix_steps)
        assert "/.DS_Store" in without.description


def test_new_probe_count_within_budget() -> None:
    """Ek Paket 1: en fazla 22 yeni istek (11 doküman yolu + 1 bağlı spec + 9 geliştirme dosyası)."""
    assert len(get_settings().web_api_doc_paths) + 1 + len(DEV_FILES) <= 22
