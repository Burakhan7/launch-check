import pytest

from launchcheck.checks.web.files import BackupExposed, EnvExposed, GitExposed, SourcemapExposed
from tests.checks.helpers import SITE, SPA_HTML, make_ctx, resp, run

ENV_BODY = "# prod\nDATABASE_URL=postgres://u:p@h/db\nexport OPENAI_API_KEY=abc\nlowercase=1\n"


async def test_env_exposed_positive_only_key_names_kept() -> None:
    ctx = make_ctx(routes={f"{SITE}/.env": resp(f"{SITE}/.env", body=ENV_BODY)})
    (finding,) = await run(EnvExposed, ctx)
    assert finding.evidence == {"path": "/.env", "keys": ["DATABASE_URL", "OPENAI_API_KEY"]}
    assert "postgres://" not in str(finding.evidence)


async def test_env_exposed_negative_404() -> None:
    assert await run(EnvExposed, make_ctx()) == []


async def test_env_spa_catch_all_is_not_a_finding() -> None:
    """SPA her yola 200 + index.html döner; bu bir .env değildir."""
    assert await run(EnvExposed, make_ctx(spa=True)) == []


async def test_env_same_as_baseline_is_not_a_finding() -> None:
    """Catch-all düz metin dönen sunucu: içerik rastgele yolla aynıysa dosya yok sayılır."""
    ctx = make_ctx()
    same = resp("x", body="A=1\n")
    ctx.fetcher.routes[f"{SITE}/.env"] = same  # type: ignore[union-attr]
    ctx.fetcher.routes[SITE + ctx._baseline_path] = same  # type: ignore[union-attr]
    assert await run(EnvExposed, ctx) == []


async def test_git_exposed_positive() -> None:
    ctx = make_ctx(
        routes={
            f"{SITE}/.git/HEAD": resp("h", body="ref: refs/heads/main\n"),
            f"{SITE}/.git/config": resp("c", body="[core]\n\trepositoryformatversion = 0\n"),
        }
    )
    (finding,) = await run(GitExposed, ctx)
    assert finding.evidence == {"paths": ["/.git/HEAD", "/.git/config"]}


@pytest.mark.parametrize("spa", [True, False])
async def test_git_exposed_negative(spa: bool) -> None:
    assert await run(GitExposed, make_ctx(spa=spa)) == []


async def test_git_head_with_wrong_content_negative() -> None:
    ctx = make_ctx(routes={f"{SITE}/.git/HEAD": resp("h", body="hello world")})
    assert await run(GitExposed, ctx) == []


async def test_backup_exposed_positive() -> None:
    ctx = make_ctx(
        routes={
            f"{SITE}/dump.sql": resp(
                "d", body="-- PostgreSQL database dump\nCREATE TABLE users();"
            ),
            f"{SITE}/backup.zip": resp("z", body=b"PK\x03\x04rest", content_type="application/zip"),
            f"{SITE}/config.json": resp(
                "j", body='{"db": {"password": "x"}}', content_type="application/json"
            ),
        }
    )
    findings = await run(BackupExposed, ctx)
    assert sorted(f.evidence["path"] for f in findings) == [
        "/backup.zip",
        "/config.json",
        "/dump.sql",
    ]


async def test_backup_negative_harmless_config_and_fake_sql() -> None:
    ctx = make_ctx(
        routes={
            f"{SITE}/config.json": resp(
                "j", body='{"theme": "dark"}', content_type="application/json"
            ),
            f"{SITE}/backup.sql": resp("s", body="just text"),
            f"{SITE}/backup.zip": resp("z", body="not a zip"),
        }
    )
    assert await run(BackupExposed, ctx) == []
    assert await run(BackupExposed, make_ctx(spa=True)) == []


MAP_BODY = '{"version":3,"sources":["../src/App.tsx"],"mappings":"AAAA"}'


async def test_sourcemap_positive_via_comment_and_default_name() -> None:
    ctx = make_ctx(
        scripts={
            f"{SITE}/assets/a.js": "code\n//# sourceMappingURL=a.js.map",
            f"{SITE}/assets/b.js?v=2": "code",
        },
        routes={
            f"{SITE}/assets/a.js.map": resp("m", body=MAP_BODY, content_type="application/json"),
            f"{SITE}/assets/b.js.map": resp("m", body=MAP_BODY[:30]),  # kesilmiş büyük map
        },
    )
    (finding,) = await run(SourcemapExposed, ctx)
    assert finding.evidence == {"files": [f"{SITE}/assets/a.js.map", f"{SITE}/assets/b.js.map"]}


async def test_sourcemap_negative() -> None:
    ctx = make_ctx(
        scripts={
            f"{SITE}/assets/a.js": "code\n//# sourceMappingURL=data:application/json;base64,e30="
        }
    )
    assert await run(SourcemapExposed, ctx) == []
    spa = make_ctx(scripts={f"{SITE}/assets/a.js": "code"}, spa=True)
    assert await run(SourcemapExposed, spa) == []


async def test_spa_html_constant() -> None:
    assert SPA_HTML.startswith(b"<!doctype html>")
