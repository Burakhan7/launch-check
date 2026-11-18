"""Test APK fixture'larını üretir. Android SDK gerekmez: apktool (aapt2 + smali) ile derlenir,
JDK'nin jarsigner'ı ile imzalanır (v1 imza).

Çalıştırma (repo kökünden):
    docker build -t lc-apk-fixtures apps/api/tests/fixtures/apk
    docker run --rm -v "$PWD/apps/api/tests/fixtures/apk:/out" lc-apk-fixtures

Sahte anahtarlar burada üretilir ve repoya düz metin olarak girmez. Değerler deterministiktir.
"""

import base64
import json
import random
import shutil
import string
import subprocess
from pathlib import Path

OUT = Path("/out")
WORK = Path("/tmp/fixtures")
ALNUM = string.ascii_letters + string.digits
HERMES_MAGIC = bytes.fromhex("c61fbc03c103191f")


def fake(prefix: str, n: int, alphabet: str = ALNUM) -> str:
    rng = random.Random(f"fixture-{prefix}-{n}")
    return prefix + "".join(rng.choice(alphabet) for _ in range(n))


def jwt(role: str) -> str:
    def b64(d: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    payload = {"iss": "supabase", "ref": "abcdefghijklmnopqrst", "role": role, "iat": 1700000000}
    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64(payload)}.{fake('', 43, ALNUM + '-_')}"


OPENAI = fake("sk-proj-", 48)
STRIPE_LIVE = fake("sk_" + "live_", 24)
GOOGLE_KEY = fake("AI" + "za", 35)
SERVICE_ROLE = jwt("service_role")
ANON = jwt("anon")
SUPABASE_URL = "https://abcdefghijklmnopqrst.supabase.co"


def manifest(package: str, app_attrs: str = "", extra: str = "", components: str = "") -> str:
    return f"""<?xml version="1.0" encoding="utf-8" standalone="no"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{package}">
    <uses-permission android:name="android.permission.INTERNET"/>
{extra}
    <application android:label="@string/app_name" {app_attrs}>
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
                <category android:name="android.intent.category.LAUNCHER"/>
            </intent-filter>
        </activity>
{components}
    </application>
</manifest>
"""


def apktool_yml(name: str, target_sdk: int, version: str = "1.0.0") -> str:
    return f"""version: 3.0.3
apkFileName: {name}.apk
usesFramework:
  ids:
  - 1
sdkInfo:
  minSdkVersion: 24
  targetSdkVersion: {target_sdk}
versionInfo:
  versionCode: 1
  versionName: {version}
resourcesInfo:
  packageId: 127
doNotCompress:
- arsc
"""


def strings_xml(extra: dict[str, str] | None = None) -> str:
    items = {"app_name": "Fixture", **(extra or {})}
    body = "\n".join(f'    <string name="{k}">{v}</string>' for k, v in items.items())
    return f'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n{body}\n</resources>\n'


def smali_class(cls: str, consts: list[str] | None = None, extra_methods: str = "") -> str:
    lines = [f'    const-string v0, "{c}"' for c in consts or []]
    method = (
        ".method public data()V\n    .registers 2\n"
        + "\n".join(lines)
        + "\n    return-void\n.end method\n"
        if lines
        else ""
    )
    return f""".class public L{cls};
.super Ljava/lang/Object;

.method public constructor <init>()V
    .registers 1
    invoke-direct {{p0}}, Ljava/lang/Object;-><init>()V
    return-void
.end method

{method}{extra_methods}"""


LOG_TOKEN_METHOD = """.method public logToken(Ljava/lang/String;)V
    .registers 4
    const-string v0, "AuthService"
    new-instance v1, Ljava/lang/StringBuilder;
    invoke-direct {v1}, Ljava/lang/StringBuilder;-><init>()V
    const-string v2, "access token: "
    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v1, p1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v1
    invoke-static {v0, v1}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I
    return-void
.end method
"""


def printable_blob(strings: list[str], size: int = 4096) -> bytes:
    """ELF benzeri ikili dosya: arada rastgele baytlar, içinde okunabilir string'ler."""
    rng = random.Random("blob-" + "|".join(strings))
    out = bytearray(b"\x7fELF\x02\x01\x01\x00")
    for s in strings:
        out += bytes(rng.randrange(0, 32) for _ in range(16)) + s.encode() + b"\x00"
    out += bytes(rng.randrange(0, 32) for _ in range(max(0, size - len(out))))
    return bytes(out)


FIXTURES: dict[str, dict[str, object]] = {
    "native_release": {
        "package": "app.lcfixture.nativeapp",
        "target": 36,
        "app_attrs": 'android:allowBackup="false"',
        "cert": "CN=Launch Check Release, O=Example",
        "smali": {"app/lcfixture/nativeapp/MainActivity": ["https://api.example.com/v1"]},
    },
    "native_debug": {
        "package": "app.lcfixture.debugapp",
        "target": 30,
        "app_attrs": 'android:debuggable="true" android:usesCleartextTraffic="true"',
        "extra": "\n".join(
            f'    <uses-permission android:name="android.permission.{p}"/>'
            for p in (
                "READ_SMS",
                "READ_CONTACTS",
                "ACCESS_BACKGROUND_LOCATION",
                "QUERY_ALL_PACKAGES",
            )
        ),
        "components": '        <service android:name=".SyncService" android:exported="true"/>\n'
        '        <receiver android:name=".BootReceiver" android:exported="false"/>',
        "cert": "CN=Android Debug, O=Android, C=US",
        "smali": {
            "app/lcfixture/debugapp/Api": [
                "http://10.0.2.2:3000/api",
                "https://staging.api.example.com/v1",
                OPENAI,
                SERVICE_ROLE,
            ]
        },
        "log_method_class": "app/lcfixture/debugapp/Api",
    },
    "flutter_debug": {
        "package": "app.lcfixture.flutterdebug",
        "target": 35,
        "app_attrs": 'android:allowBackup="false"',
        "cert": "CN=Android Debug, O=Android, C=US",
        "smali": {"io/flutter/embedding/android/FlutterActivity": []},
        "files": {
            "lib/arm64-v8a/libflutter.so": printable_blob(["Flutter engine"]),
            "assets/flutter_assets/kernel_blob.bin": b"\x90\xab\xcd\xef" + bytes(256),
            "assets/flutter_assets/AssetManifest.json": b"{}",
        },
    },
    "flutterflow_release": {
        "package": "app.lcfixture.ffapp",
        "target": 36,
        "app_attrs": 'android:allowBackup="false"',
        "cert": "CN=FF Release, O=Example",
        "smali": {"io/flutter/embedding/android/FlutterActivity": []},
        "files": {
            "lib/arm64-v8a/libflutter.so": printable_blob(["Flutter engine"]),
            "lib/arm64-v8a/libapp.so": printable_blob(
                [
                    "package:ff_app/flutter_flow/flutter_flow_theme.dart",
                    "package:ff_app/flutter_flow/flutter_flow_util.dart",
                    STRIPE_LIVE,
                    SUPABASE_URL,
                ]
            ),
            "assets/flutter_assets/AssetManifest.json": b'{"assets/.env":["assets/.env"]}',
            "assets/flutter_assets/assets/.env": f"SUPABASE_URL={SUPABASE_URL}\nSUPABASE_ANON_KEY={ANON}\n".encode(),
        },
    },
    "rn_release": {
        "package": "app.lcfixture.rnapp",
        "target": 36,
        "app_attrs": 'android:allowBackup="false"',
        "cert": "CN=RN Release, O=Example",
        "strings": {
            "google_api_key": GOOGLE_KEY,
            "project_id": "rn-fixture-project",
            "firebase_database_url": "https://rn-fixture-project.firebaseio.com",
        },
        "smali": {"com/facebook/react/ReactActivity": []},
        "files": {
            "assets/index.android.bundle": HERMES_MAGIC
            + printable_blob([SUPABASE_URL, "https://api.example.com/graphql", ANON], 2048),
        },
    },
    "expo_dev": {
        "package": "app.lcfixture.expodev",
        "target": 35,
        "app_attrs": 'android:allowBackup="false"',
        "cert": "CN=Android Debug, O=Android, C=US",
        "smali": {
            "com/facebook/react/ReactActivity": [],
            "expo/modules/devlauncher/DevLauncherController": ["http://10.0.2.2:8081"],
        },
    },
}


def write_project(name: str, spec: dict[str, object]) -> Path:
    root = WORK / name
    shutil.rmtree(root, ignore_errors=True)
    (root / "res/values").mkdir(parents=True)
    (root / "AndroidManifest.xml").write_text(
        manifest(
            str(spec["package"]),
            str(spec.get("app_attrs", "")),
            str(spec.get("extra", "")),
            str(spec.get("components", "")),
        )
    )
    (root / "apktool.yml").write_text(apktool_yml(name, int(spec["target"])))  # type: ignore[call-overload]
    (root / "res/values/strings.xml").write_text(strings_xml(spec.get("strings")))  # type: ignore[arg-type]
    for cls, consts in spec.get("smali", {}).items():  # type: ignore[attr-defined]
        path = root / "smali" / f"{cls}.smali"
        path.parent.mkdir(parents=True, exist_ok=True)
        extra = LOG_TOKEN_METHOD if spec.get("log_method_class") == cls else ""
        path.write_text(smali_class(cls, consts, extra))
    for rel, data in spec.get("files", {}).items():  # type: ignore[attr-defined]
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def run(*cmd: str) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def build(name: str, spec: dict[str, object]) -> None:
    root = write_project(name, spec)
    unsigned = WORK / f"{name}-unsigned.apk"
    run("java", "-jar", "/opt/apktool.jar", "b", str(root), "-o", str(unsigned))
    keystore = WORK / f"{name}.jks"
    keystore.unlink(missing_ok=True)
    run(
        "keytool",
        "-genkeypair",
        "-keystore",
        str(keystore),
        "-storepass",
        "fixture",
        "-keypass",
        "fixture",
        "-alias",
        "key",
        "-keyalg",
        "RSA",
        "-keysize",
        "2048",
        "-validity",
        "10000",
        "-dname",
        str(spec["cert"]),
    )
    signed = OUT / f"{name}.apk"
    run(
        "jarsigner",
        "-keystore",
        str(keystore),
        "-storepass",
        "fixture",
        "-sigalg",
        "SHA256withRSA",
        "-digestalg",
        "SHA-256",
        "-signedjar",
        str(signed),
        str(unsigned),
        "key",
    )
    print(f"{signed.name}: {signed.stat().st_size} bayt")


if __name__ == "__main__":
    WORK.mkdir(parents=True, exist_ok=True)
    for fixture_name, fixture_spec in FIXTURES.items():
        build(fixture_name, fixture_spec)
