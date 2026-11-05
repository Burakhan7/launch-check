"""APK yükleme güvenliği (AGENTS.md §8).

- Uzantı + magic bytes (ZIP `PK\\x03\\x04`) kontrolü, boyut limiti (akış sırasında)
- Zip bomb: toplam açılmış boyut, dosya sayısı ve sıkıştırma oranı limiti
- Path traversal: `../`, mutlak yol, sürücü harfi, ters bölü, NUL içeren girdiler reddedilir
- Rastgele adla kaydedilir; worker'a sadece dosya ADI gider, yol değil
"""

import asyncio
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

ZIP_MAGIC = b"PK\x03\x04"
APK_EXTENSION = ".apk"
CHUNK_SIZE = 1024 * 1024
UPLOAD_NAME_RE = re.compile(r"^[0-9a-f]{32}\.apk$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
# Bu boyuttan küçük girdilerde sıkıştırma oranına bakılmaz (küçük dosyalar doğal olarak çok sıkışır)
RATIO_CHECK_MIN_BYTES = 1024 * 1024
REQUIRED_ENTRY = "AndroidManifest.xml"


class UploadError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class ZipLimits:
    max_entries: int
    max_uncompressed_bytes: int
    max_compression_ratio: float


@dataclass(frozen=True)
class ZipStats:
    entries: int
    uncompressed_bytes: int


def validate_filename(filename: str | None) -> None:
    if not filename or not filename.lower().endswith(APK_EXTENSION):
        raise UploadError("invalid_file_type")


def is_unsafe_member(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name:
        return True
    if name.startswith("/") or _DRIVE_RE.match(name):
        return True
    return ".." in PurePosixPath(name).parts


def inspect_zip(path: Path, limits: ZipLimits) -> ZipStats:
    """Senkron; `asyncio.to_thread` ile çağrılır. Worker'da da tekrarlanır (derin savunma)."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise UploadError("invalid_apk") from exc

    if len(infos) > limits.max_entries:
        raise UploadError("zip_bomb")
    total = 0
    names = set()
    for info in infos:
        if is_unsafe_member(info.filename):
            raise UploadError("invalid_apk")
        total += info.file_size
        if total > limits.max_uncompressed_bytes:
            raise UploadError("zip_bomb")
        ratio_applies = info.file_size >= RATIO_CHECK_MIN_BYTES
        if ratio_applies and info.file_size > info.compress_size * limits.max_compression_ratio:
            raise UploadError("zip_bomb")
        names.add(info.filename)
    if REQUIRED_ENTRY not in names:
        raise UploadError("invalid_apk")
    return ZipStats(entries=len(infos), uncompressed_bytes=total)


def _open_new_file(upload_dir: Path) -> tuple[Path, BinaryIO]:
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{uuid.uuid4().hex}{APK_EXTENSION}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    return path, os.fdopen(os.open(path, flags, 0o600), "wb")


async def save_upload(source: AsyncReadable, upload_dir: Path, max_bytes: int) -> Path:
    """Dosyayı akış hâlinde rastgele bir adla kaydeder. Limit aşılırsa veya magic bytes
    tutmazsa dosyayı siler ve UploadError atar. Disk işlemleri thread'de yapılır."""
    path, out = await asyncio.to_thread(_open_new_file, upload_dir)
    size = 0
    try:
        with out:
            first = True
            while chunk := await source.read(CHUNK_SIZE):
                if first and not chunk.startswith(ZIP_MAGIC):
                    raise UploadError("invalid_apk")
                first = False
                size += len(chunk)
                if size > max_bytes:
                    raise UploadError("apk_too_large")
                await asyncio.to_thread(out.write, chunk)
        if size == 0:
            raise UploadError("invalid_apk")
    except BaseException:
        await asyncio.to_thread(path.unlink, missing_ok=True)
        raise
    return path


def resolve_upload(name: str, upload_dir: Path) -> Path:
    """Worker tarafı: job argümanındaki dosya adını yükleme klasörü içinde güvenle çözer."""
    if not UPLOAD_NAME_RE.match(name):
        raise UploadError("upload_missing")
    path = (upload_dir / name).resolve()
    if path.parent != upload_dir.resolve():
        raise UploadError("upload_missing")
    return path


def delete_upload(name: str, upload_dir: Path) -> None:
    try:
        resolve_upload(name, upload_dir).unlink(missing_ok=True)
    except UploadError:
        return
