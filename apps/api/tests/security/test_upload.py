import io
import zipfile
from pathlib import Path

import pytest

from launchcheck.security.upload import (
    UploadError,
    ZipLimits,
    delete_upload,
    inspect_zip,
    is_unsafe_member,
    resolve_upload,
    save_upload,
    validate_filename,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "apk"
LIMITS = ZipLimits(
    max_entries=100, max_uncompressed_bytes=50 * 1024 * 1024, max_compression_ratio=200
)


def make_zip(
    path: Path, entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED
) -> Path:
    with zipfile.ZipFile(path, "w", compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


class Chunks:
    def __init__(self, data: bytes, chunk: int = 1000) -> None:
        self.buf = io.BytesIO(data)
        self.chunk = chunk

    async def read(self, size: int = -1) -> bytes:
        return self.buf.read(min(size, self.chunk))


@pytest.mark.parametrize("name", ["app.apk", "APP.APK", "my.app.apk"])
def test_valid_filenames(name: str) -> None:
    validate_filename(name)


@pytest.mark.parametrize("name", [None, "", "app.zip", "app.apk.exe", "app.aab", "apk"])
def test_invalid_filenames(name: str | None) -> None:
    with pytest.raises(UploadError) as exc:
        validate_filename(name)
    assert exc.value.code == "invalid_file_type"


@pytest.mark.parametrize(
    "name",
    ["../evil.so", "assets/../../etc/passwd", "/etc/passwd", "C:/Windows/x", "a\\b", "a\x00b", ""],
)
def test_unsafe_member_names(name: str) -> None:
    assert is_unsafe_member(name)


@pytest.mark.parametrize("name", ["AndroidManifest.xml", "assets/a..b.txt", "lib/arm64-v8a/x.so"])
def test_safe_member_names(name: str) -> None:
    assert not is_unsafe_member(name)


def test_real_fixture_apk_passes() -> None:
    stats = inspect_zip(FIXTURES / "native_release.apk", LIMITS)
    assert stats.entries >= 3


def test_path_traversal_rejected(tmp_path: Path) -> None:
    apk = make_zip(tmp_path / "a.apk", {"AndroidManifest.xml": b"x", "../../evil.sh": b"x"})
    with pytest.raises(UploadError) as exc:
        inspect_zip(apk, LIMITS)
    assert exc.value.code == "invalid_apk"


def test_missing_manifest_rejected(tmp_path: Path) -> None:
    apk = make_zip(tmp_path / "a.apk", {"hello.txt": b"x"})
    with pytest.raises(UploadError) as exc:
        inspect_zip(apk, LIMITS)
    assert exc.value.code == "invalid_apk"


def test_not_a_zip_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.apk"
    path.write_bytes(b"PK\x03\x04 but broken")
    with pytest.raises(UploadError) as exc:
        inspect_zip(path, LIMITS)
    assert exc.value.code == "invalid_apk"


def test_too_many_entries(tmp_path: Path) -> None:
    entries = {"AndroidManifest.xml": b"x", **{f"f{i}": b"x" for i in range(5)}}
    apk = make_zip(tmp_path / "a.apk", entries)
    with pytest.raises(UploadError) as exc:
        inspect_zip(apk, ZipLimits(5, 10**9, 200))
    assert exc.value.code == "zip_bomb"


def test_total_uncompressed_limit(tmp_path: Path) -> None:
    apk = make_zip(tmp_path / "a.apk", {"AndroidManifest.xml": b"x", "big": b"a" * 5000})
    with pytest.raises(UploadError) as exc:
        inspect_zip(apk, ZipLimits(100, 4000, 10**6))
    assert exc.value.code == "zip_bomb"


def test_compression_ratio_bomb(tmp_path: Path) -> None:
    """10 MB sıfır ~10 KB'a sıkışır (oran ~1000)."""
    apk = make_zip(
        tmp_path / "a.apk", {"AndroidManifest.xml": b"x", "zeros": bytes(10 * 1024 * 1024)}
    )
    with pytest.raises(UploadError) as exc:
        inspect_zip(apk, LIMITS)
    assert exc.value.code == "zip_bomb"


def test_small_highly_compressible_file_is_fine(tmp_path: Path) -> None:
    apk = make_zip(tmp_path / "a.apk", {"AndroidManifest.xml": bytes(500_000)})
    inspect_zip(apk, LIMITS)


async def test_save_upload_ok_and_private(tmp_path: Path) -> None:
    data = (FIXTURES / "native_release.apk").read_bytes()
    path = await save_upload(Chunks(data), tmp_path / "up", max_bytes=10**6)
    assert path.read_bytes() == data
    assert path.parent == tmp_path / "up"
    assert len(path.stem) == 32  # rastgele ad, kullanıcının dosya adı değil


@pytest.mark.parametrize(
    ("data", "limit", "code"),
    [
        (b"PK\x03\x04" + b"a" * 5000, 4000, "apk_too_large"),
        (b"MZ\x90\x00 not a zip", 10**6, "invalid_apk"),
        (b"", 10**6, "invalid_apk"),
    ],
)
async def test_save_upload_rejects_and_leaves_nothing(
    tmp_path: Path, data: bytes, limit: int, code: str
) -> None:
    with pytest.raises(UploadError) as exc:
        await save_upload(Chunks(data), tmp_path, max_bytes=limit)
    assert exc.value.code == code
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name", ["../x.apk", "/etc/passwd", "a" * 32 + ".apk/../../x", "short.apk", "A" * 32 + ".apk"]
)
def test_resolve_upload_rejects_bad_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(UploadError):
        resolve_upload(name, tmp_path)


def test_delete_upload_only_inside_dir(tmp_path: Path) -> None:
    inside = tmp_path / "up"
    inside.mkdir()
    target = inside / ("a" * 32 + ".apk")
    target.write_bytes(b"x")
    outside = tmp_path / "keep.txt"
    outside.write_bytes(b"x")
    delete_upload("../keep.txt", inside)
    delete_upload(target.name, inside)
    assert outside.exists()
    assert not target.exists()
