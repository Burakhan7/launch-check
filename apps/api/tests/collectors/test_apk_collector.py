"""Gerçek apktool/jadx ile APK collector testleri. Araçlar yoksa (ör. Windows, CI api job'u)
atlanır; worker imajında `pytest -m tools` ile çalıştırılır."""

import asyncio
import shutil
import uuid
import zipfile
from pathlib import Path

import pytest

from launchcheck.collectors.apk_collector import ApkCollector, ApkContext
from launchcheck.collectors.base import CollectorError
from launchcheck.config import Settings

FIXTURES = Path(__file__).parent.parent / "fixtures" / "apk"
pytestmark = [
    pytest.mark.tools,
    pytest.mark.skipif(
        shutil.which("apktool") is None or shutil.which("jadx") is None,
        reason="apktool/jadx kurulu değil",
    ),
]


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    upload, work = tmp_path / "uploads", tmp_path / "work"
    upload.mkdir()
    work.mkdir()
    return upload, work


def settings(dirs: tuple[Path, Path], **overrides: object) -> Settings:
    return Settings(upload_dir=str(dirs[0]), scan_tmp_dir=str(dirs[1]), **overrides)  # type: ignore[arg-type]


def stage(fixture: str, upload_dir: Path) -> str:
    name = f"{uuid.uuid4().hex}.apk"
    shutil.copy(FIXTURES / f"{fixture}.apk", upload_dir / name)
    return name


def assert_clean(dirs: tuple[Path, Path]) -> None:
    assert list(dirs[0].iterdir()) == [], "yüklenen APK silinmedi"
    assert list(dirs[1].iterdir()) == [], "geçici klasör silinmedi"


@pytest.mark.parametrize(
    ("fixture", "package", "framework", "target_sdk", "debug"),
    [
        ("native_release", "com.example.nativeapp", "native", 36, False),
        ("native_debug", "com.example.debugapp", "native", 30, True),
        ("flutter_debug", "com.example.flutterdebug", "flutter", 35, True),
        ("flutterflow_release", "com.example.ffapp", "flutterflow", 36, False),
        ("rn_release", "com.example.rnapp", "react_native", 36, False),
        ("expo_dev", "com.example.expodev", "expo", 35, True),
    ],
)
async def test_collect_fixture(
    dirs: tuple[Path, Path],
    fixture: str,
    package: str,
    framework: str,
    target_sdk: int,
    debug: bool,
) -> None:
    name = stage(fixture, dirs[0])
    ctx = await ApkCollector(settings(dirs)).collect(uuid.uuid4(), {"upload_path": name})
    assert isinstance(ctx, ApkContext)
    try:
        assert ctx.target_label == package
        assert ctx.meta["package"] == package
        assert ctx.framework == framework == ctx.meta["framework"]
        assert ctx.target_sdk == target_sdk
        assert ctx.signing.is_debug is debug
        assert ctx.application is not None
        assert ctx.string_sources
    finally:
        await ctx.aclose()
    assert_clean(dirs)


async def test_string_sources_cover_all_locations(dirs: tuple[Path, Path]) -> None:
    ctx = await ApkCollector(settings(dirs)).collect(
        uuid.uuid4(), {"upload_path": stage("flutterflow_release", dirs[0])}
    )
    assert isinstance(ctx, ApkContext)
    try:
        names = [n for n, _ in ctx.string_sources]
        assert "lib/libapp.so" in names
        assert "assets/flutter_assets/assets/.env" in names  # paketlenmiş .env
        assert ctx.meta["supabase_urls"] == ["https://abcdefghijklmnopqrst.supabase.co"]
    finally:
        await ctx.aclose()


async def test_rn_meta_hermes_and_firebase(dirs: tuple[Path, Path]) -> None:
    ctx = await ApkCollector(settings(dirs)).collect(
        uuid.uuid4(), {"upload_path": stage("rn_release", dirs[0])}
    )
    try:
        assert ctx.meta["hermes"] is True
        firebase = ctx.meta["firebase"]
        assert firebase["project_id"] == "rn-fixture-project"
        assert "…" in firebase["google_api_key"]  # maskeli
        assert "assets/index.android.bundle" in [n for n, _ in ctx.string_sources]  # type: ignore[attr-defined]
    finally:
        await ctx.aclose()


async def test_smali_const_strings_and_jadx(dirs: tuple[Path, Path]) -> None:
    ctx = await ApkCollector(settings(dirs)).collect(
        uuid.uuid4(), {"upload_path": stage("native_debug", dirs[0])}
    )
    assert isinstance(ctx, ApkContext)
    try:
        smali = dict(ctx.string_sources)["smali/com/example/debugapp/Api.smali"]
        assert "http://10.0.2.2:3000/api" in smali
        assert ctx.meta["decompiled_sources"] is True
        java = dict(ctx.jadx_sources())
        assert any("Log.d" in text for text in java.values())
    finally:
        await ctx.aclose()


async def test_corrupt_apk_fails_and_cleans_up(dirs: tuple[Path, Path]) -> None:
    name = f"{uuid.uuid4().hex}.apk"
    with zipfile.ZipFile(dirs[0] / name, "w") as zf:
        zf.writestr("AndroidManifest.xml", b"\x03\x00garbage-not-axml")
        zf.writestr("classes.dex", b"dex\n035\x00garbage")
    with pytest.raises(CollectorError) as exc:
        await ApkCollector(settings(dirs)).collect(uuid.uuid4(), {"upload_path": name})
    assert exc.value.code == "apk_decode_failed"
    assert_clean(dirs)


async def test_tool_timeout_fails_and_cleans_up(dirs: tuple[Path, Path]) -> None:
    name = stage("native_release", dirs[0])
    with pytest.raises(CollectorError) as exc:
        await ApkCollector(settings(dirs, apktool_timeout_s=0.01)).collect(
            uuid.uuid4(), {"upload_path": name}
        )
    assert exc.value.code == "apk_timeout"
    assert_clean(dirs)


async def test_cancellation_cleans_up(dirs: tuple[Path, Path]) -> None:
    """Tarama zaman aşımı (pipeline'daki asyncio.timeout) collect'i iptal eder."""
    name = stage("native_release", dirs[0])
    task = asyncio.ensure_future(
        ApkCollector(settings(dirs)).collect(uuid.uuid4(), {"upload_path": name})
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_clean(dirs)


async def test_missing_upload(dirs: tuple[Path, Path]) -> None:
    with pytest.raises(CollectorError) as exc:
        await ApkCollector(settings(dirs)).collect(
            uuid.uuid4(), {"upload_path": f"{uuid.uuid4().hex}.apk"}
        )
    assert exc.value.code == "upload_missing"
    with pytest.raises(CollectorError):
        await ApkCollector(settings(dirs)).collect(
            uuid.uuid4(), {"upload_path": "../../etc/passwd"}
        )
