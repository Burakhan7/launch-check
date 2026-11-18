"""APK check'leri: her biri için pozitif + negatif (sentetik bağlam, araç gerekmez)."""

import json
from pathlib import Path

import pytest

from launchcheck.checks.apk.build import DebugCertificate, Debuggable, FlutterDebug, RnDevMode
from launchcheck.checks.apk.log_leaks import SensitiveLog
from launchcheck.checks.apk.manifest import (
    AllowBackup,
    ExamplePackage,
    ExportedComponents,
    SensitivePermissions,
    TargetSdkTooLow,
)
from launchcheck.checks.apk.network import Cleartext, LocalhostUrls, StagingUrls
from launchcheck.checks.apk.secrets import GoogleApiKey, LeakedKey, ServiceRole
from launchcheck.checks.base import Severity
from launchcheck.config import get_settings
from tests.checks.apk_helpers import make_apk_ctx, manifest, run
from tests.checks.helpers import ALNUM, fake_supabase_jwt, fake_token

STRIPE = fake_token("sk_" + "live_", 24)
GOOGLE = fake_token("AI" + "za", 35, ALNUM + "_-")


# --- secrets


async def test_leaked_key_positive(tmp_path: Path) -> None:
    ctx = make_apk_ctx(tmp_path, sources=[("lib/libapp.so", f"x\n{STRIPE}\ny")])
    (finding,) = await run(LeakedKey, ctx)
    assert finding.severity == Severity.CRITICAL
    assert finding.evidence["locations"] == [{"file": "lib/libapp.so", "line": 2}]
    assert STRIPE not in json.dumps(finding.evidence)


async def test_leaked_key_negative(tmp_path: Path) -> None:
    ctx = make_apk_ctx(tmp_path, sources=[("res/values/strings.xml", "<string>hello</string>")])
    assert await run(LeakedKey, ctx) == []


async def test_google_key_separate(tmp_path: Path) -> None:
    ctx = make_apk_ctx(tmp_path, sources=[("res/values/strings.xml", f"<string>{GOOGLE}</string>")])
    assert await run(LeakedKey, ctx) == []
    assert len(await run(GoogleApiKey, ctx)) == 1
    assert await run(GoogleApiKey, make_apk_ctx(tmp_path)) == []


async def test_service_role(tmp_path: Path) -> None:
    service = make_apk_ctx(
        tmp_path, sources=[("assets/.env", f"KEY={fake_supabase_jwt('service_role')}")]
    )
    assert len(await run(ServiceRole, service)) == 1
    anon = make_apk_ctx(tmp_path, sources=[("assets/.env", f"KEY={fake_supabase_jwt('anon')}")])
    assert await run(ServiceRole, anon) == []


# --- build


async def test_debuggable(tmp_path: Path) -> None:
    on = make_apk_ctx(tmp_path, manifest(app_attrs='android:debuggable="true"'))
    assert len(await run(Debuggable, on)) == 1
    off = make_apk_ctx(tmp_path, manifest(app_attrs='android:debuggable="false"'))
    assert await run(Debuggable, off) == []
    assert await run(Debuggable, make_apk_ctx(tmp_path)) == []


async def test_debug_certificate(tmp_path: Path) -> None:
    (finding,) = await run(DebugCertificate, make_apk_ctx(tmp_path, debug=True))
    assert finding.evidence["common_name"] == "Android Debug"
    assert await run(DebugCertificate, make_apk_ctx(tmp_path, debug=False)) == []


async def test_flutter_debug(tmp_path: Path) -> None:
    entries = ["lib/arm64-v8a/libflutter.so", "assets/flutter_assets/kernel_blob.bin"]
    assert len(await run(FlutterDebug, make_apk_ctx(tmp_path, entries=entries))) == 1
    release = ["lib/arm64-v8a/libflutter.so", "lib/arm64-v8a/libapp.so"]
    assert await run(FlutterDebug, make_apk_ctx(tmp_path, entries=release)) == []


async def test_rn_dev_mode_no_bundle(tmp_path: Path) -> None:
    ctx = make_apk_ctx(tmp_path, framework="react_native")
    (finding,) = await run(RnDevMode, ctx)
    assert finding.evidence["reasons"] == ["no_js_bundle"]


async def test_rn_dev_mode_expo_dev_client(tmp_path: Path) -> None:
    ctx = make_apk_ctx(
        tmp_path,
        framework="expo",
        entries=["assets/index.android.bundle"],
        smali_packages=["expo/modules/devlauncher"],
    )
    (finding,) = await run(RnDevMode, ctx)
    assert finding.evidence == {
        "reasons": ["expo_dev_client"],
        "packages": ["expo/modules/devlauncher"],
    }


async def test_rn_dev_mode_negative(tmp_path: Path) -> None:
    release = make_apk_ctx(
        tmp_path, framework="react_native", entries=["assets/index.android.bundle"]
    )
    assert await run(RnDevMode, release) == []
    assert await run(RnDevMode, make_apk_ctx(tmp_path, framework="native")) == []


# --- network


async def test_localhost_and_lan_positive(tmp_path: Path) -> None:
    ctx = make_apk_ctx(
        tmp_path,
        sources=[
            (
                "smali/a.smali",
                '"http://10.0.2.2:3000/api" "http://192.168.1.20:8000" "http://localhost/x"',
            )
        ],
    )
    (finding,) = await run(LocalhostUrls, ctx)
    assert set(finding.evidence["urls"]) == {
        "http://10.0.2.2:3000/api",
        "http://192.168.1.20:8000",
        "http://localhost/x",
    }


async def test_localhost_negative_rn_tooling_and_public(tmp_path: Path) -> None:
    ctx = make_apk_ctx(
        tmp_path,
        framework="react_native",
        entries=["assets/index.android.bundle"],
        sources=[
            (
                "smali/a.smali",
                '"http://localhost:8081/status" "https://api.example.com" "http://localhost:4318/v1"',
            )
        ],
    )
    assert await run(LocalhostUrls, ctx) == []


async def test_metro_port_flagged_for_native_apps(tmp_path: Path) -> None:
    ctx = make_apk_ctx(tmp_path, sources=[("smali/a.smali", '"http://localhost:8081/api"')])
    assert len(await run(LocalhostUrls, ctx)) == 1


async def test_staging(tmp_path: Path) -> None:
    pos = make_apk_ctx(tmp_path, sources=[("a", '"https://staging.api.example.com/v1"')])
    assert len(await run(StagingUrls, pos)) == 1
    neg = make_apk_ctx(
        tmp_path, sources=[("a", '"https://api.example.com" "https://developer.android.com/x"')]
    )
    assert await run(StagingUrls, neg) == []


async def test_cleartext_manifest(tmp_path: Path) -> None:
    ctx = make_apk_ctx(tmp_path, manifest(app_attrs='android:usesCleartextTraffic="true"'))
    (finding,) = await run(Cleartext, ctx)
    assert finding.evidence["reasons"] == ["manifest_uses_cleartext_traffic"]


async def test_cleartext_default_for_old_target(tmp_path: Path) -> None:
    (finding,) = await run(Cleartext, make_apk_ctx(tmp_path, target_sdk=27))
    assert finding.evidence["reasons"] == ["default_for_old_target_sdk"]


async def test_cleartext_network_security_config(tmp_path: Path) -> None:
    nsc = (
        "<network-security-config>"
        '<base-config cleartextTrafficPermitted="true"/>'
        '<domain-config cleartextTrafficPermitted="true"><domain>api.example.com</domain>'
        "<domain>10.0.2.2</domain></domain-config></network-security-config>"
    )
    ctx = make_apk_ctx(
        tmp_path,
        manifest(app_attrs='android:networkSecurityConfig="@xml/network_security_config"'),
        res_xml={"network_security_config": nsc},
    )
    (finding,) = await run(Cleartext, ctx)
    assert finding.evidence["reasons"] == [
        "network_security_config_base",
        "network_security_config_domains",
    ]
    assert finding.evidence["domains"] == ["api.example.com"]


async def test_cleartext_http_api_urls(tmp_path: Path) -> None:
    ctx = make_apk_ctx(
        tmp_path,
        sources=[
            (
                "a",
                '"http://api.myapp.com/login" "http://myapp.com/v1/users" "http://schemas.android.com/apk/res/android"',
            )
        ],
    )
    (finding,) = await run(Cleartext, ctx)
    assert finding.evidence["urls"] == ["http://api.myapp.com/login", "http://myapp.com/v1/users"]


async def test_cleartext_negative(tmp_path: Path) -> None:
    nsc = '<network-security-config><domain-config cleartextTrafficPermitted="true"><domain>localhost</domain></domain-config></network-security-config>'
    ctx = make_apk_ctx(
        tmp_path,
        manifest(app_attrs='android:networkSecurityConfig="@xml/nsc"'),
        res_xml={"nsc": nsc},
        sources=[("a", '"http://www.w3.org/2000/svg" "https://api.myapp.com/v1"')],
    )
    assert await run(Cleartext, ctx) == []


# --- manifest


async def test_target_sdk_too_low(tmp_path: Path) -> None:
    required = get_settings().play_min_target_sdk
    (finding,) = await run(TargetSdkTooLow, make_apk_ctx(tmp_path, target_sdk=required - 1))
    assert finding.evidence == {"target_sdk": required - 1, "required": required}
    assert await run(TargetSdkTooLow, make_apk_ctx(tmp_path, target_sdk=required)) == []
    assert await run(TargetSdkTooLow, make_apk_ctx(tmp_path, target_sdk=None)) == []


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        ('android:allowBackup="true"', "true"),
        ("", "default"),
        ('android:allowBackup="false"', None),
        ('android:allowBackup="true" android:dataExtractionRules="@xml/rules"', None),
    ],
)
async def test_allow_backup(tmp_path: Path, attrs: str, expected: str | None) -> None:
    findings = await run(AllowBackup, make_apk_ctx(tmp_path, manifest(app_attrs=attrs)))
    assert [f.evidence["value"] for f in findings] == ([expected] if expected else [])


async def test_exported_components(tmp_path: Path) -> None:
    body = (
        '<service android:name=".Sync" android:exported="true"/>'
        '<provider android:name=".Files" android:exported="true"/>'
        '<receiver android:name=".Guarded" android:exported="true" android:permission="com.x.P"/>'
        '<receiver android:name=".Private" android:exported="false"/>'
        '<receiver android:name=".Boot" android:exported="true"><intent-filter>'
        '<action android:name="android.intent.action.BOOT_COMPLETED"/></intent-filter></receiver>'
    )
    (finding,) = await run(ExportedComponents, make_apk_ctx(tmp_path, manifest(body=body)))
    assert finding.evidence["components"] == [
        {"type": "service", "name": ".Sync"},
        {"type": "provider", "name": ".Files"},
    ]
    assert await run(ExportedComponents, make_apk_ctx(tmp_path)) == []


async def test_sensitive_permissions(tmp_path: Path) -> None:
    extra = "".join(
        f'<uses-permission android:name="android.permission.{p}"/>'
        for p in ("INTERNET", "READ_SMS", "QUERY_ALL_PACKAGES", "CAMERA")
    )
    (finding,) = await run(SensitivePermissions, make_apk_ctx(tmp_path, manifest(extra=extra)))
    assert finding.evidence == {"permissions": ["READ_SMS", "QUERY_ALL_PACKAGES"]}
    harmless = '<uses-permission android:name="android.permission.INTERNET"/>'
    assert await run(SensitivePermissions, make_apk_ctx(tmp_path, manifest(extra=harmless))) == []


# --- logging


async def test_sensitive_log_only_app_package(tmp_path: Path) -> None:
    jadx = {
        "com/example/app/Auth.java": 'class A {\n void f(String t) {\n  Log.d("Auth", "token=" + t);\n }\n}',
        "com/google/firebase/Lib.java": 'Log.d("FB", "Token refresh");',
    }
    (finding,) = await run(SensitiveLog, make_apk_ctx(tmp_path, jadx=jadx))
    assert finding.evidence["locations"] == [
        {"file": "sources/com/example/app/Auth.java", "line": 3}
    ]


async def test_sensitive_log_negative(tmp_path: Path) -> None:
    jadx = {"com/example/app/Main.java": 'Log.d("Main", "started");\nString token = load();'}
    assert await run(SensitiveLog, make_apk_ctx(tmp_path, jadx=jadx)) == []
    assert await run(SensitiveLog, make_apk_ctx(tmp_path)) == []  # jadx yok


# --- paket adı


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        ("com.example.app", (Severity.HIGH, "play_rejects")),
        ("com.example", (Severity.HIGH, "play_rejects")),
        ("com.mycompany.app", (Severity.LOW, "placeholder_namespace")),
        ("io.flutter.demo", (Severity.LOW, "placeholder_namespace")),
        ("com.examplecorp.app", None),  # önek tam segment olmalı
        ("com.bglabs.app", None),
        ("com.testflight.app", None),
    ],
)
async def test_example_package(
    tmp_path: Path, package: str, expected: tuple[Severity, str] | None
) -> None:
    ctx = make_apk_ctx(tmp_path, manifest_xml=manifest(package=package))
    findings = await run(ExamplePackage, ctx)
    if expected is None:
        assert findings == []
        return
    (finding,) = findings
    assert (finding.severity, finding.evidence["reason"]) == expected
    assert finding.evidence["package"] == package


async def test_example_package_empty_manifest_package(tmp_path: Path) -> None:
    ctx = make_apk_ctx(tmp_path, manifest_xml=manifest(package=""))
    assert await run(ExamplePackage, ctx) == []


def test_example_package_texts_by_reason() -> None:
    from launchcheck.i18n import finding_text, fragment_fn

    check = ExamplePackage()
    for lang in ("tr", "en"):
        fragment = fragment_fn(check.id, lang)
        high = finding_text(
            check.id,
            lang,
            check.text_params({"package": "com.example.app", "reason": "play_rejects"}, fragment),
        )
        low = finding_text(
            check.id,
            lang,
            check.text_params(
                {"package": "com.mycompany.app", "reason": "placeholder_namespace"}, fragment
            ),
        )
        assert "com.example.app" in high.description and "com.example" in high.why_it_matters
        assert "{" not in high.description + high.why_it_matters + high.fix_prompt
        assert low.why_it_matters != high.why_it_matters
