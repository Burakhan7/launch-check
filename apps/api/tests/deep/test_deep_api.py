"""Faz 2 API: POST /deep ön koşulları, rakip doğrulama, kota, kuyruk; olaylar; asset'ler; rapor."""

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.checks.base import Category, Severity, TargetType
from launchcheck.config import get_settings
from launchcheck.db.models import Scan, ScanAsset, ScanEvent, ScanStatus
from launchcheck.deep.assets import AssetStore
from tests.conftest import FakeQueue, FakeResolver
from tests.deep.helpers import add_deep, add_parent

SM = async_sessionmaker[AsyncSession]
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8


async def test_create_deep_scan(client: AsyncClient, queue: FakeQueue, sessionmaker: SM) -> None:
    parent = await add_parent(sessionmaker)
    resp = await client.post(
        f"/api/scans/{parent}/deep",
        json={"competitors": ["Rakip.io/fiyat?x=1", "https://rakip.io"], "use_reference": False},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["queue_position"] == 1 and body["eta_s"] == get_settings().deep_default_duration_s
    deep_id = uuid.UUID(body["scan_id"])

    async with sessionmaker() as s:
        scan = await s.get(Scan, deep_id)
    assert scan is not None
    assert (scan.phase, scan.parent_scan_id, scan.status) == (2, parent, ScanStatus.QUEUED)
    assert scan.options == {"competitors": ["https://rakip.io"], "use_reference": False}
    function, args, kwargs = queue.jobs[0]
    assert (function, args) == ("run_deep_scan", (str(deep_id),))
    assert kwargs == {"_job_id": str(deep_id), "_queue_name": "arq:deep"}


async def test_parent_not_found(client: AsyncClient) -> None:
    for scan_id in (uuid.uuid4(), "not-a-uuid"):
        resp = await client.post(f"/api/scans/{scan_id}/deep", json={})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "parent_not_found"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": ScanStatus.RUNNING},
        {"status": ScanStatus.FAILED},
        {"target_type": TargetType.APK},
    ],
)
async def test_parent_not_ready(client: AsyncClient, sessionmaker: SM, kwargs: dict) -> None:  # type: ignore[type-arg]
    parent = await add_parent(sessionmaker, **kwargs)
    resp = await client.post(f"/api/scans/{parent}/deep", json={})
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "parent_not_ready")


async def test_deep_scan_of_deep_scan_not_allowed(client: AsyncClient, sessionmaker: SM) -> None:
    deep = await add_deep(sessionmaker, await add_parent(sessionmaker), status=ScanStatus.DONE)
    resp = await client.post(f"/api/scans/{deep}/deep", json={})
    assert resp.json()["error"]["code"] == "parent_not_ready"


async def test_parent_too_old(client: AsyncClient, sessionmaker: SM) -> None:
    parent = await add_parent(sessionmaker, age=timedelta(hours=25))
    resp = await client.post(f"/api/scans/{parent}/deep", json={})
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "parent_too_old")


async def test_competitor_validation(
    client: AsyncClient, sessionmaker: SM, resolver: FakeResolver
) -> None:
    parent = await add_parent(sessionmaker)
    resolver.mapping["intranet.example.org"] = ["10.0.0.5"]
    cases = {
        "intranet.example.org": "invalid_competitor_url",
        "ftp://rakip.io": "invalid_competitor_url",
        "https://blog.example.com": "competitor_same_origin",
        "example.com": "competitor_same_origin",
    }
    for url, code in cases.items():
        resp = await client.post(f"/api/scans/{parent}/deep", json={"competitors": [url]})
        assert (resp.status_code, resp.json()["error"]["code"]) == (400, code), url
    resp = await client.post(
        f"/api/scans/{parent}/deep", json={"competitors": ["a.io", "b.io", "c.io", "d.io"]}
    )
    assert resp.status_code == 422  # en fazla 3 rakip


async def test_compare_to_validation(client: AsyncClient, sessionmaker: SM) -> None:
    parent = await add_parent(sessionmaker)
    queued_deep = await add_deep(sessionmaker, parent)
    for compare_to in (str(uuid.uuid4()), str(queued_deep), str(parent)):
        resp = await client.post(f"/api/scans/{parent}/deep", json={"compare_to": compare_to})
        assert resp.json()["error"]["code"] == "invalid_compare_to", compare_to
    done_deep = await add_deep(sessionmaker, parent, status=ScanStatus.DONE)
    resp = await client.post(f"/api/scans/{parent}/deep", json={"compare_to": str(done_deep)})
    assert resp.status_code == 202


async def test_queue_full(
    client: AsyncClient, sessionmaker: SM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "deep_queue_max", 2)
    parent = await add_parent(sessionmaker)
    for _ in range(2):
        await add_deep(sessionmaker, parent)
    resp = await client.post(f"/api/scans/{parent}/deep", json={})
    assert (resp.status_code, resp.json()["error"]["code"]) == (503, "queue_full")


async def test_daily_rate_limit_only_counts_valid_requests(
    client: AsyncClient, sessionmaker: SM
) -> None:
    old = await add_parent(sessionmaker, age=timedelta(days=2))
    for _ in range(5):  # geçersiz istekler kotayı harcamaz
        await client.post(f"/api/scans/{old}/deep", json={})
    parent = await add_parent(sessionmaker)
    for _ in range(get_settings().rate_limit_deep_per_day):
        assert (await client.post(f"/api/scans/{parent}/deep", json={})).status_code == 202
    resp = await client.post(f"/api/scans/{parent}/deep", json={})
    assert (resp.status_code, resp.json()["error"]["code"]) == (429, "deep_rate_limited")
    assert "Retry-After" in resp.headers


async def test_queue_failure_marks_deep_failed(
    sessionmaker: SM, client: AsyncClient, queue: FakeQueue
) -> None:
    queue.fail = True
    parent = await add_parent(sessionmaker)
    resp = await client.post(f"/api/scans/{parent}/deep", json={})
    assert (resp.status_code, resp.json()["error"]["code"]) == (503, "queue_unavailable")


async def test_queue_position_and_eta(client: AsyncClient, sessionmaker: SM) -> None:
    parent = await add_parent(sessionmaker)
    await add_deep(
        sessionmaker, parent, status=ScanStatus.RUNNING, started_ago=timedelta(minutes=1)
    )
    await add_deep(sessionmaker, parent, age=timedelta(seconds=30))
    resp = await client.post(f"/api/scans/{parent}/deep", json={})
    body = resp.json()
    assert body["queue_position"] == 3
    assert body["eta_s"] == 3 * get_settings().deep_default_duration_s

    report = (await client.get(f"/api/scans/{body['scan_id']}")).json()
    assert report["queue_position"] == 3 and report["phase"] == 2
    assert report["parent_scan_id"] == str(parent)


async def test_deep_report_fields_and_expiry(client: AsyncClient, sessionmaker: SM) -> None:
    from launchcheck.deep.orchestrator import DeepOrchestrator, OrchestratorConfig

    parent = await add_parent(
        sessionmaker,
        age=timedelta(hours=20),
        findings=[("web.files.env_exposed", Severity.CRITICAL, Category.SECURITY)],
    )
    deep = await add_deep(sessionmaker, parent)
    await DeepOrchestrator(
        sessionmaker, OrchestratorConfig(settings=get_settings(), modules=[])
    ).run(deep)

    report = (await client.get(f"/api/scans/{deep}?lang=en")).json()
    (finding,) = report["findings"]
    assert finding["source_phase"] == 1 and finding["verification"] == "not_verifiable"
    assert report["queue_position"] is None
    parent_report = (await client.get(f"/api/scans/{parent}")).json()
    assert report["expires_at"] == parent_report["expires_at"]  # Faz 1 ile birlikte silinir
    # Faz 2 check'leri modülü çalışmadan "geçti" sayılmaz
    assert "web.conversion.value_proposition" not in {p["check_id"] for p in report["passed"]}
    assert "web.files.git_exposed" in {p["check_id"] for p in report["passed"]}


async def test_events_paging_and_i18n(client: AsyncClient, sessionmaker: SM) -> None:
    deep = await add_deep(sessionmaker, await add_parent(sessionmaker))
    async with sessionmaker() as s:
        s.add_all(
            [
                ScanEvent(scan_id=deep, module="core", key="deep.event.started", params={}),
                ScanEvent(
                    scan_id=deep,
                    module="render",
                    key="deep.event.module_done",
                    params={"module": "render", "found": 7},
                ),
            ]
        )
        await s.commit()
    tr = (await client.get(f"/api/scans/{deep}/events?lang=tr")).json()
    assert [e["text"] for e in tr["events"]] == [
        "Derin tarama başladı.",
        "Gerçek ziyaretçi görünümü bitti: 7 bulgu.",
    ]
    assert tr["status"] == "queued"
    first_id = tr["events"][0]["id"]
    en = (await client.get(f"/api/scans/{deep}/events?lang=en&after={first_id}")).json()
    assert [e["text"] for e in en["events"]] == ["Real visitor view finished: 7 findings."]
    assert (await client.get(f"/api/scans/{uuid.uuid4()}/events")).status_code == 404


async def test_assets_served_only_for_own_scan(
    client: AsyncClient, sessionmaker: SM, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "assets_dir", str(tmp_path))
    parent = await add_parent(sessionmaker)
    deep = await add_deep(sessionmaker, parent)
    other = await add_deep(sessionmaker, parent)
    store = AssetStore(tmp_path, 1024 * 1024)
    asset = store.save(deep, "screenshot_mobile", "https://example.com/", WEBP, 375, 812)
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    evil = ScanAsset(
        scan_id=deep, kind="x", page_url="/", path="../secret.txt", width=1, height=1, bytes=1
    )
    async with sessionmaker() as s:
        s.add_all([asset, evil])
        await s.commit()

    ok = await client.get(f"/api/scans/{deep}/assets/{asset.id}")
    assert ok.status_code == 200 and ok.content == WEBP
    assert ok.headers["content-type"] == "image/webp"
    assert ok.headers["cache-control"] == "private, max-age=3600"
    assert ok.headers["x-robots-tag"] == "noindex"
    assert (await client.get(f"/api/scans/{other}/assets/{asset.id}")).status_code == 404
    assert (await client.get(f"/api/scans/{deep}/assets/{evil.id}")).status_code == 404
    assert (await client.get(f"/api/scans/{deep}/assets/{uuid.uuid4()}")).status_code == 404
    assert (await client.get(f"/api/scans/{deep}/assets/nope")).status_code == 404
