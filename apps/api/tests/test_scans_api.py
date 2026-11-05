import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.api import report as report_module
from launchcheck.checks.base import Category, Severity, TargetType
from launchcheck.db.models import FindingRow, Scan, ScanStatus
from launchcheck.i18n.loader import FindingText
from tests.conftest import FakeQueue


async def test_create_web_scan(
    client: AsyncClient, queue: FakeQueue, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    resp = await client.post("/api/scans/web", json={"url": "Example.com/app?secret=1"})
    assert resp.status_code == 202
    scan_id = uuid.UUID(resp.json()["scan_id"])

    async with sessionmaker() as s:
        scan = await s.get(Scan, scan_id)
    assert scan is not None
    assert scan.status == ScanStatus.QUEUED
    assert scan.target_label == "https://example.com"  # yol/query DB'ye yazılmaz

    function, args, kwargs = queue.jobs[0]
    assert function == "run_scan"
    assert args == (str(scan_id), {"url": "https://example.com/app"})
    assert kwargs["_job_id"] == str(scan_id)


@pytest.mark.parametrize(
    ("lang", "expected"), [("tr", "geçerli bir web adresi"), ("en", "valid web address")]
)
async def test_invalid_url_error_is_localized(
    client: AsyncClient, lang: str, expected: str
) -> None:
    resp = await client.post(f"/api/scans/web?lang={lang}", json={"url": "not a url"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_url"
    assert expected in body["error"]["message"]


async def test_disallowed_port(client: AsyncClient) -> None:
    resp = await client.post("/api/scans/web", json={"url": "https://example.com:8443"})
    assert resp.json()["error"]["code"] == "url_port_not_allowed"


async def test_validation_error_format(client: AsyncClient) -> None:
    resp = await client.post("/api/scans/web", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"


async def test_queue_failure_marks_scan_failed(
    client: AsyncClient, queue: FakeQueue, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    queue.fail = True
    resp = await client.post("/api/scans/web", json={"url": "https://example.com"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "queue_unavailable"


@pytest.mark.parametrize("scan_id", [str(uuid.uuid4()), "not-a-uuid"])
async def test_get_unknown_scan_404(client: AsyncClient, scan_id: str) -> None:
    resp = await client.get(f"/api/scans/{scan_id}?lang=en")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_get_done_scan_report(
    client: AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_text(check_id: str, lang: str) -> FindingText:
        return FindingText(f"{lang}:{check_id}", "d", "w", ["s1", "s2"], "p")

    monkeypatch.setattr(report_module, "finding_text", fake_text)
    scan_id = uuid.uuid4()
    async with sessionmaker() as s:
        s.add(
            Scan(
                id=scan_id,
                target_type=TargetType.WEB,
                target_label="https://example.com",
                status=ScanStatus.DONE,
                score=49,
                category_scores={"security": 58, "prod_readiness": 98},
                meta={"platform": ["lovable"], "checks_failed": []},
                created_at=datetime(2026, 11, 1, tzinfo=UTC),
            )
        )
        s.add_all(
            [
                FindingRow(
                    scan_id=scan_id,
                    check_id="web.headers.csp_missing",
                    severity=Severity.LOW,
                    category=Category.SECURITY,
                    evidence={},
                ),
                FindingRow(
                    scan_id=scan_id,
                    check_id="web.secrets.service_role",
                    severity=Severity.CRITICAL,
                    category=Category.SECURITY,
                    evidence={"masked": "eyJhbG…abcd"},
                ),
            ]
        )
        await s.commit()

    resp = await client.get(f"/api/scans/{scan_id}?lang=en")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["score_label"] == "not_ready"
    assert body["meta"] == {"platform": ["lovable"]}  # iç alanlar gizli
    assert [f["check_id"] for f in body["findings"]] == [
        "web.secrets.service_role",  # önce critical
        "web.headers.csp_missing",
    ]
    assert body["findings"][0]["title"] == "en:web.secrets.service_role"
    assert body["findings"][0]["fix_steps"] == ["s1", "s2"]
    assert body["findings"][0]["evidence"] == {"masked": "eyJhbG…abcd"}
    assert body["expires_at"].startswith("2026-11-08")
    assert body["error"] is None


async def test_get_failed_scan_shows_localized_error(
    client: AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    scan_id = uuid.uuid4()
    async with sessionmaker() as s:
        s.add(
            Scan(
                id=scan_id,
                target_type=TargetType.WEB,
                target_label="https://example.com",
                status=ScanStatus.FAILED,
                error_code="scan_timeout",
            )
        )
        await s.commit()
    body = (await client.get(f"/api/scans/{scan_id}?lang=tr")).json()
    assert body["error"]["code"] == "scan_timeout"
    assert "çok uzun sürdü" in body["error"]["message"]
    assert body["findings"] == [] and body["passed"] == []
