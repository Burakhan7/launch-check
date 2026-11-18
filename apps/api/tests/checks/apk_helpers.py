"""Araç gerektirmeyen APK check testleri için sentetik ApkContext."""

from pathlib import Path

from defusedxml import ElementTree as SafeET

from launchcheck.checks.base import Check, Finding, TargetType
from launchcheck.collectors.apk_collector import ApkContext
from launchcheck.collectors.apk_signing import SigningInfo

PACKAGE = "com.example.app"
NS = 'xmlns:android="http://schemas.android.com/apk/res/android"'


def manifest(
    app_attrs: str = 'android:allowBackup="false"',
    body: str = "",
    extra: str = "",
    package: str = PACKAGE,
) -> str:
    return (
        f'<manifest {NS} package="{package}">{extra}'
        f"<application {app_attrs}>"
        '<activity android:name=".Main" android:exported="true"><intent-filter>'
        '<action android:name="android.intent.action.MAIN"/></intent-filter></activity>'
        f"{body}</application></manifest>"
    )


def make_apk_ctx(
    tmp_path: Path,
    manifest_xml: str | None = None,
    target_sdk: int | None = 36,
    debug: bool = False,
    entries: list[str] | None = None,
    sources: list[tuple[str, str]] | None = None,
    framework: str = "native",
    res_xml: dict[str, str] | None = None,
    smali_packages: list[str] | None = None,
    jadx: dict[str, str] | None = None,
) -> ApkContext:
    decoded = tmp_path / "decoded"
    (decoded / "smali").mkdir(parents=True, exist_ok=True)
    for name, xml in (res_xml or {}).items():
        path = decoded / "res" / "xml" / f"{name}.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(xml)
    for package in smali_packages or []:
        (decoded / "smali" / package).mkdir(parents=True, exist_ok=True)
    jadx_dir = None
    if jadx is not None:
        jadx_dir = tmp_path / "jadx"
        for rel, text in jadx.items():
            path = jadx_dir / "sources" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    return ApkContext(
        target_type=TargetType.APK,
        target_label=PACKAGE,
        work_dir=tmp_path,
        decoded_dir=decoded,
        jadx_dir=jadx_dir,
        zip_entries=entries or ["AndroidManifest.xml", "classes.dex"],
        manifest=SafeET.fromstring(manifest_xml or manifest()),
        apktool_info={"sdkInfo": {"minSdkVersion": 24, "targetSdkVersion": target_sdk}},
        signing=SigningInfo(["v2"], ["Android Debug" if debug else "Release Co"]),
        framework=framework,
        string_sources=sources or [],
    )


async def run(check: type[Check], ctx: ApkContext) -> list[Finding]:
    return await check().run(ctx)
