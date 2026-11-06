"""APK collector: apktool + jadx + imza + framework tespiti → ApkContext.

Temizlik garantisi: yüklenen APK ve geçici çalışma klasörü
- collect başarısız olursa (hata, zaman aşımı, iptal) burada,
- başarılı olursa `ApkContext.aclose()` ile (pipeline her durumda çağırır),
- worker job'u hiç collect'e ulaşmazsa `run_scan`'in finally bloğunda,
- worker çökerse cron (yetim yükleme temizliği) ile silinir.
"""

import asyncio
import logging
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

import yaml
from defusedxml import ElementTree as SafeET

from launchcheck.checks.base import ScanContext, TargetType
from launchcheck.checks.shared.masking import mask_secret
from launchcheck.collectors.apk_framework import RN_BUNDLE, detect_framework, is_hermes_bundle
from launchcheck.collectors.apk_signing import SigningInfo, read_signing_info
from launchcheck.collectors.apk_strings import (
    decode_text,
    looks_like_text,
    printable_strings,
    smali_const_strings,
)
from launchcheck.collectors.base import CollectorError
from launchcheck.collectors.platform import backend_hints
from launchcheck.collectors.tools import ToolError, ToolTimeout, run_tool, tool_env
from launchcheck.config import Settings
from launchcheck.security.upload import UploadError, ZipLimits, inspect_zip, resolve_upload

logger = logging.getLogger(__name__)

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
PREFERRED_ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")
FIREBASE_STRING_KEYS = ("google_api_key", "project_id", "firebase_database_url", "google_app_id")
TEXT_ASSET_PREFIXES = ("assets/", "res/raw/")
SKIPPED_ASSETS = ("assets/flutter_assets/kernel_blob.bin",)


def android_attr(node: Element, name: str) -> str | None:
    return node.get(f"{ANDROID_NS}{name}")


@dataclass
class ApkContext(ScanContext):
    work_dir: Path = Path()
    apk_path: Path = Path()
    decoded_dir: Path = Path()
    jadx_dir: Path | None = None
    zip_entries: list[str] = field(default_factory=list)
    manifest: Element | None = None
    apktool_info: dict[str, Any] = field(default_factory=dict)
    signing: SigningInfo = field(default_factory=SigningInfo)
    framework: str = "native"
    string_sources: list[tuple[str, str]] = field(default_factory=list)

    @property
    def package(self) -> str:
        return self.manifest.get("package", "") if self.manifest is not None else ""

    @property
    def target_sdk(self) -> int | None:
        return _as_int((self.apktool_info.get("sdkInfo") or {}).get("targetSdkVersion"))

    @property
    def min_sdk(self) -> int | None:
        return _as_int((self.apktool_info.get("sdkInfo") or {}).get("minSdkVersion"))

    @property
    def application(self) -> Element | None:
        return self.manifest.find("application") if self.manifest is not None else None

    def read_entry(self, name: str, max_bytes: int) -> bytes | None:
        with zipfile.ZipFile(self.apk_path) as archive:
            try:
                info = archive.getinfo(name)
            except KeyError:
                return None
            with archive.open(info) as fh:
                return fh.read(max_bytes)

    def jadx_sources(self) -> list[tuple[str, str]]:
        """jadx ile çevrilmiş Java kaynakları (jadx başarısızsa boş)."""
        if self.jadx_dir is None or not self.jadx_dir.exists():
            return []
        return [
            (p.relative_to(self.jadx_dir).as_posix(), p.read_text("utf-8", errors="replace"))
            for p in sorted(self.jadx_dir.rglob("*.java"))
        ]

    async def aclose(self) -> None:
        cleanup(self.work_dir, self.apk_path)


def _as_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def cleanup(work_dir: Path | None, apk_path: Path | None) -> None:
    if work_dir is not None and work_dir != Path() and work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    if apk_path is not None and apk_path != Path():
        apk_path.unlink(missing_ok=True)


class _Budget:
    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def take(self, text: str) -> bool:
        if len(text) > self.remaining:
            return False
        self.remaining -= len(text)
        return True


class ApkCollector:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    @property
    def _limits(self) -> ZipLimits:
        return ZipLimits(
            max_entries=self._s.max_apk_entries,
            max_uncompressed_bytes=self._s.max_apk_uncompressed_bytes,
            max_compression_ratio=self._s.max_apk_compression_ratio,
        )

    async def collect(self, scan_id: uuid.UUID, args: dict[str, Any]) -> ScanContext:
        upload_dir = Path(self._s.upload_dir)
        try:
            apk_path = resolve_upload(str(args.get("upload_path", "")), upload_dir)
        except UploadError as exc:
            raise CollectorError(exc.code) from exc
        work_dir = Path(self._s.scan_tmp_dir) / scan_id.hex
        try:
            if not apk_path.is_file():
                raise CollectorError("upload_missing")
            work_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
            return await self._collect(apk_path, work_dir)
        except UploadError as exc:
            cleanup(work_dir, apk_path)
            raise CollectorError(exc.code) from exc
        except BaseException:
            cleanup(work_dir, apk_path)
            raise

    async def _collect(self, apk_path: Path, work_dir: Path) -> ApkContext:
        await asyncio.to_thread(inspect_zip, apk_path, self._limits)  # derin savunma
        decoded = work_dir / "decoded"
        await self._apktool(apk_path, decoded, work_dir)
        jadx_dir = await self._jadx(apk_path, work_dir)

        ctx = ApkContext(
            target_type=TargetType.APK,
            target_label="",
            work_dir=work_dir,
            apk_path=apk_path,
            decoded_dir=decoded,
            jadx_dir=jadx_dir,
        )
        with zipfile.ZipFile(apk_path) as archive:
            ctx.zip_entries = archive.namelist()
        ctx.manifest = _parse_manifest(decoded / "AndroidManifest.xml")
        ctx.apktool_info = _parse_apktool_yml(decoded / "apktool.yml")
        ctx.signing = await asyncio.to_thread(read_signing_info, apk_path)
        ctx.target_label = ctx.package

        libapp = self._libapp_strings(ctx)
        ctx.framework = str(detect_framework(ctx.zip_entries, _smali_packages(decoded), libapp))
        ctx.string_sources = await asyncio.to_thread(self._string_sources, ctx, libapp)
        ctx.meta = self._meta(ctx)
        return ctx

    async def _apktool(self, apk: Path, out: Path, work_dir: Path) -> None:
        args = [
            "apktool",
            "d",
            "-q",
            "-f",
            "--no-assets",
            "-p",
            str(work_dir / "fw"),
            "-o",
            str(out),
            str(apk),
        ]
        try:
            await run_tool(
                args, work_dir, tool_env(work_dir, self._s.java_max_heap), self._s.apktool_timeout_s
            )
        except ToolTimeout as exc:
            raise CollectorError("apk_timeout") from exc
        except ToolError as exc:
            raise CollectorError("apk_decode_failed") from exc
        if not (out / "AndroidManifest.xml").is_file():
            raise CollectorError("apk_decode_failed")

    async def _jadx(self, apk: Path, work_dir: Path) -> Path | None:
        """jadx isteğe bağlı: zaman aşımı veya hata olursa onsuz devam edilir."""
        out = work_dir / "jadx"
        args = ["jadx", "-q", "--no-res", "-j", str(self._s.jadx_threads), "-d", str(out), str(apk)]
        try:
            await run_tool(
                args, work_dir, tool_env(work_dir, self._s.java_max_heap), self._s.jadx_timeout_s
            )
        except ToolError:
            # jadx bazı sınıflarda hata koduyla biter ama çıktının çoğu kullanılabilir.
            logger.info("jadx tamamlanamadı; mevcut çıktıyla devam ediliyor")
        return out if out.exists() else None

    def _libapp_strings(self, ctx: ApkContext) -> str:
        for abi in PREFERRED_ABIS:
            data = ctx.read_entry(f"lib/{abi}/libapp.so", self._s.max_apk_uncompressed_bytes)
            if data is not None:
                return printable_strings(data)
        return ""

    def _string_sources(self, ctx: ApkContext, libapp: str) -> list[tuple[str, str]]:
        budget = _Budget(self._s.apk_max_string_bytes)
        sources: list[tuple[str, str]] = []

        def add(name: str, text: str) -> None:
            if text.strip() and budget.take(text):
                sources.append((name, text))

        for path in sorted(ctx.decoded_dir.glob("res/values*/strings.xml")):
            add(
                path.relative_to(ctx.decoded_dir).as_posix(),
                path.read_text("utf-8", errors="replace"),
            )
        for path in sorted(ctx.decoded_dir.glob("smali*/**/*.smali")):
            add(
                path.relative_to(ctx.decoded_dir).as_posix(),
                smali_const_strings(path.read_text("utf-8", errors="replace")),
            )
        if libapp:
            add("lib/libapp.so", libapp)
        for name in ctx.zip_entries:
            if (
                name.startswith(TEXT_ASSET_PREFIXES)
                and name not in SKIPPED_ASSETS
                and not name.endswith("/")
            ):
                data = ctx.read_entry(name, self._s.apk_max_asset_bytes)
                if not data:
                    continue
                if name == RN_BUNDLE and is_hermes_bundle(data):
                    add(name, printable_strings(data))
                elif looks_like_text(data):
                    add(name, decode_text(data))
        for name, text in ctx.jadx_sources():
            add(f"jadx/{name}", text)
        return sources

    def _meta(self, ctx: ApkContext) -> dict[str, Any]:
        version = ctx.apktool_info.get("versionInfo") or {}
        meta: dict[str, Any] = {
            "package": ctx.package,
            "framework": ctx.framework,
            "version_name": str(version.get("versionName") or ""),
            "version_code": _as_int(version.get("versionCode")),
            "min_sdk": ctx.min_sdk,
            "target_sdk": ctx.target_sdk,
            "signature_schemes": ctx.signing.schemes,
            "decompiled_sources": ctx.jadx_dir is not None,
            "files_scanned": len(ctx.string_sources),
        }
        if ctx.framework == "react_native":
            head = ctx.read_entry(RN_BUNDLE, 16) or b""
            meta["hermes"] = is_hermes_bundle(head)
        firebase = _firebase_values(ctx.decoded_dir)
        if firebase:
            meta["firebase"] = firebase
        meta.update(backend_hints(text for _, text in ctx.string_sources))
        return meta


def _parse_manifest(path: Path) -> Element:
    try:
        return SafeET.parse(path).getroot()
    except Exception as exc:  # defusedxml + bozuk XML
        raise CollectorError("apk_decode_failed") from exc


def _parse_apktool_yml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text("utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _smali_packages(decoded: Path) -> set[str]:
    packages = set()
    for smali_root in decoded.glob("smali*"):
        for directory in smali_root.rglob("*"):
            if directory.is_dir():
                packages.add(directory.relative_to(smali_root).as_posix())
    return packages


def _firebase_values(decoded: Path) -> dict[str, str]:
    """google-services.json'dan üretilen değerler (Faz 3 hazırlığı). API anahtarı maskelenir."""
    path = decoded / "res/values/strings.xml"
    if not path.is_file():
        return {}
    try:
        root = SafeET.parse(path).getroot()
    except Exception:
        return {}
    values = {
        node.get("name", ""): (node.text or "").strip()
        for node in root.findall("string")
        if node.get("name") in FIREBASE_STRING_KEYS
    }
    if values.get("google_api_key"):
        values["google_api_key"] = mask_secret(values["google_api_key"])
    return {k: v for k, v in values.items() if v}
