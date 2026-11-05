"""POST /api/scans/apk ve worker tarafı yükleme temizliği."""

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

from launchcheck.config import get_settings
from launchcheck.worker import tasks
from launchcheck.worker.cron import delete_orphan_uploads
from tests.conftest import FakeQueue

FIXTURE = Path(__file__).parent / "fixtures" / "apk" / "native_release.apk"


@pytest.fixture
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "uploads"
    monkeypatch.setattr(get_settings(), "upload_dir", str(path))
    return path


def files(name: str = "my-app.apk", data: bytes | None = None) -> dict[str, tuple[str, bytes]]:
    return {"file": (name, FIXTURE.read_bytes() if data is None else data)}


async def test_apk_upload_accepted(client: AsyncClient, queue: FakeQueue, upload_dir: Path) -> None:
    resp = await client.post("/api/scans/apk", files=files(), data={"consent": "true"})
    assert resp.status_code == 202, resp.text
    (stored,) = list(upload_dir.iterdir())
    assert "my-app" not in stored.name  # kullanıcının dosya adı saklanmaz
    assert stored.read_bytes() == FIXTURE.read_bytes()
    function, args, _ = queue.jobs[0]
    assert function == "run_scan"
    assert args[1] == {"upload_path": stored.name}  # worker'a yol değil, sadece ad gider


@pytest.mark.parametrize(
    ("kwargs", "consent", "code", "status"),
    [
        ({}, None, "consent_required", 400),
        ({}, "false", "consent_required", 400),
        ({"name": "app.zip"}, "true", "invalid_file_type", 400),
        ({"data": b"MZ\x90 not a zip"}, "true", "invalid_apk", 400),
        ({"data": b"PK\x03\x04broken zip"}, "true", "invalid_apk", 400),
    ],
)
async def test_apk_upload_rejected_and_nothing_stored(
    client: AsyncClient,
    queue: FakeQueue,
    upload_dir: Path,
    kwargs: dict[str, object],
    consent: str | None,
    code: str,
    status: int,
) -> None:
    data = {"consent": consent} if consent is not None else {}
    resp = await client.post("/api/scans/apk", files=files(**kwargs), data=data)  # type: ignore[arg-type]
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code
    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []
    assert queue.jobs == []


async def test_apk_too_large(
    client: AsyncClient, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_apk_bytes", 1000)
    resp = await client.post("/api/scans/apk", files=files(), data={"consent": "true"})
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "apk_too_large"
    assert list(upload_dir.iterdir()) == []


async def test_queue_failure_deletes_upload(
    client: AsyncClient, queue: FakeQueue, upload_dir: Path
) -> None:
    queue.fail = True
    resp = await client.post("/api/scans/apk", files=files(), data={"consent": "true"})
    assert resp.status_code == 503
    assert list(upload_dir.iterdir()) == []


async def test_apk_rate_limit(
    client: AsyncClient, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_apk_per_hour", 1)
    first = await client.post("/api/scans/apk", files=files(), data={"consent": "true"})
    second = await client.post("/api/scans/apk", files=files(), data={"consent": "true"})
    assert (first.status_code, second.status_code) == (202, 429)


async def test_run_scan_always_deletes_upload(
    upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """execute_scan hata verse bile (ör. collector'a hiç ulaşılmadı) yükleme silinir."""
    upload_dir.mkdir()
    name = f"{uuid.uuid4().hex}.apk"
    (upload_dir / name).write_bytes(b"x")

    async def boom(*_: object) -> None:
        raise RuntimeError

    monkeypatch.setattr(tasks, "execute_scan", boom)
    with pytest.raises(RuntimeError):
        await tasks.run_scan({"sessionmaker": None}, str(uuid.uuid4()), {"upload_path": name})
    assert list(upload_dir.iterdir()) == []


async def test_orphan_cleanup(tmp_path: Path) -> None:
    import os
    import time

    uploads, work = tmp_path / "u", tmp_path / "w"
    uploads.mkdir()
    (work / "scan1").mkdir(parents=True)
    old, fresh = uploads / "old.apk", uploads / "new.apk"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(old, (past, past))
    os.utime(work / "scan1", (past, past))
    assert await delete_orphan_uploads(uploads, work, max_age_s=3600) == 2
    assert [p.name for p in uploads.iterdir()] == ["new.apk"]
    assert list(work.iterdir()) == []
