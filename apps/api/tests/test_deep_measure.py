"""P11 ölçüm aracı: yüzdelik, docker stats ayrıştırma, özet ve uçtan uca akış (respx, ağ yok)."""

import httpx
import pytest
import respx

from launchcheck.tools import deep_measure as dm
from launchcheck.tools.deep_measure import SiteResult

API = "http://api.test"


def test_percentile_nearest_rank() -> None:
    values = [600, 300, 900, 420, 480, 510, 700, 1000]
    assert dm.percentile(values, 50) == 510
    assert dm.percentile(values, 90) == 1000
    assert dm.percentile([42], 90) == 42
    assert dm.percentile([], 50) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.5GiB / 6GiB", int(1.5 * 1024**3)),
        ("512MiB / 6GiB", 512 * 1024**2),
        ("900kB / 1GB", 900_000),
        ("--", None),
        ("", None),
    ],
)
def test_parse_mem(text: str, expected: int | None) -> None:
    assert dm.parse_mem(text) == expected


def test_parse_cpu() -> None:
    assert dm.parse_cpu("312.45%") == 312.45
    assert dm.parse_cpu("--") is None


def _done(label: str, total: float, **modules: float) -> SiteResult:
    return SiteResult(
        label=label,
        url=f"https://{label}.test",
        status="done",
        deep_duration_s=total,
        module_durations=modules,
        peak_mem_bytes=int(total) * 1024**2,
    )


def test_summary_flags_target_and_hard_limit() -> None:
    results = [
        _done("a", 500, render=200, links=60),
        _done("b", 700, render=240, links=90),
        _done("c", 1300, render=240),
        SiteResult(label="d", url="https://d.test", status="error", error="TimeoutError"),
    ]
    results[2].partial_modules = ["render"]
    results[1].failed_modules = ["links"]
    summary = dm.summarize(results)
    assert (summary["sites"], summary["done"]) == (4, 3)
    assert summary["total_p50_s"] == 700 and summary["total_p90_s"] == 1300
    assert summary["meets_p90_target"] is False
    assert summary["over_hard_limit"] == ["c"]
    assert summary["modules"]["render"] == {
        "p50": 240,
        "p90": 240,
        "max": 240,
        "partial": 1,
        "failed": 0,
    }
    assert summary["modules"]["links"]["failed"] == 1
    assert summary["modules"]["links"]["p90"] == 90
    assert summary["peak_mem_bytes"] == 1300 * 1024**2

    table = dm.markdown(results, summary)
    assert "| links | 60 sn | 1.5 dk | 1.5 dk | 0 | 1 |" in table
    assert "| c | done |" in table and "| d | error |" in table
    assert "p90 ≤ 15 dk: hayır" in table


def test_summary_without_finished_scans() -> None:
    summary = dm.summarize([SiteResult(label="x", url="https://x.test", status="error")])
    assert summary["total_p90_s"] is None and summary["meets_p90_target"] is False


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dm, "POLL_S", 0)


@respx.mock
async def test_measure_site_end_to_end() -> None:
    respx.post(f"{API}/api/scans/web").respond(202, json={"scan_id": "p1"})
    respx.get(f"{API}/api/scans/p1").mock(
        side_effect=[
            httpx.Response(200, json={"status": "running"}),
            httpx.Response(200, json={"status": "done"}),
        ]
    )
    deep = respx.post(f"{API}/api/scans/p1/deep").respond(
        202, json={"scan_id": "d1", "queue_position": 0, "eta_s": 840}
    )
    respx.get(f"{API}/api/scans/d1").respond(
        200,
        json={
            "status": "done",
            "findings": [{}, {}],
            "meta": {
                "deep": {
                    "duration_s": 610,
                    "module_durations": {"render": 230.5},
                    "partial_modules": ["render"],
                    "failed_modules": [],
                }
            },
        },
    )
    async with httpx.AsyncClient(base_url=API) as client:
        result = await dm.measure_site(client, "vite", "https://vite.test", None)
    assert result.status == "done" and result.deep_duration_s == 610
    assert result.module_durations == {"render": 230.5}
    assert result.partial_modules == ["render"] and result.findings == 2
    assert result.phase1_s is not None and result.deep_wall_s is not None
    assert deep.calls.last.request.content == b'{"use_reference":true}'


@respx.mock
async def test_measure_site_reports_rejection_and_errors() -> None:
    respx.post(f"{API}/api/scans/web").respond(202, json={"scan_id": "p1"})
    respx.get(f"{API}/api/scans/p1").respond(200, json={"status": "done"})
    respx.post(f"{API}/api/scans/p1/deep").respond(
        429, json={"error": {"code": "deep_rate_limited"}}
    )
    async with httpx.AsyncClient(base_url=API) as client:
        rejected = await dm.measure_site(client, "x", "https://x.test", None)
    assert rejected.status == "deep_rejected" and "deep_rate_limited" in (rejected.error or "")

    respx.post(f"{API}/api/scans/web").mock(side_effect=httpx.ConnectTimeout("t"))
    async with httpx.AsyncClient(base_url=API) as client:
        failed = await dm.measure_site(client, "y", "https://y.test", None)
    assert (failed.status, failed.error) == ("error", "ConnectTimeout")


@respx.mock
async def test_measure_site_phase1_failure() -> None:
    respx.post(f"{API}/api/scans/web").respond(202, json={"scan_id": "p1"})
    respx.get(f"{API}/api/scans/p1").respond(
        200, json={"status": "failed", "error": {"code": "fetch_failed", "message": "x"}}
    )
    async with httpx.AsyncClient(base_url=API) as client:
        result = await dm.measure_site(client, "z", "https://z.test", None)
    assert (result.status, result.error) == ("phase1_failed", "fetch_failed")
    assert result.deep_duration_s is None


def test_parse_targets() -> None:
    assert dm._parse_targets(["wp=https://a.test", "https://b.test"]) == [
        ("wp", "https://a.test"),
        ("https://b.test", "https://b.test"),
    ]
