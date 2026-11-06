"""Uçtan uca: gerçek fixture APK'lar → collector → tüm APK check'leri. Her fixture için
çıkan bulgular TAM OLARAK beklenen küme olmalı (eksik = kaçırılan sorun, fazla = yanlış alarm)."""

import json
import shutil
import uuid
from pathlib import Path

import pytest

from launchcheck.checks.base import Severity, TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.collectors.apk_collector import ApkCollector
from launchcheck.config import Settings
from launchcheck.scoring import compute_score

FIXTURES = Path(__file__).parent.parent / "fixtures" / "apk"
pytestmark = [
    pytest.mark.tools,
    pytest.mark.skipif(shutil.which("apktool") is None, reason="apktool kurulu değil"),
]

EXPECTED: dict[str, set[str]] = {
    "native_release": set(),
    "native_debug": {
        "apk.build.debuggable",
        "apk.build.debug_certificate",
        "apk.network.cleartext",
        "apk.network.localhost_urls",
        "apk.network.staging_urls",
        "apk.sdk.target_too_low",
        "apk.manifest.allow_backup",
        "apk.manifest.exported_components",
        "apk.permissions.sensitive",
        "apk.secrets.leaked_key",
        "apk.secrets.service_role",
        "apk.logging.sensitive_log",
    },
    "flutter_debug": {
        "apk.build.debug_certificate",
        "apk.build.flutter_debug",
        "apk.sdk.target_too_low",
    },
    "flutterflow_release": {"apk.secrets.leaked_key"},
    "rn_release": {"apk.secrets.google_api_key"},
    "expo_dev": {
        "apk.build.debug_certificate",
        "apk.build.rn_dev_mode",
        "apk.sdk.target_too_low",
    },
}


@pytest.mark.parametrize("fixture", list(EXPECTED))
async def test_fixture_findings_exact(tmp_path: Path, fixture: str) -> None:
    upload, work = tmp_path / "u", tmp_path / "w"
    upload.mkdir()
    work.mkdir()
    name = f"{uuid.uuid4().hex}.apk"
    shutil.copy(FIXTURES / f"{fixture}.apk", upload / name)
    settings = Settings(upload_dir=str(upload), scan_tmp_dir=str(work), play_min_target_sdk=36)

    ctx = await ApkCollector(settings).collect(uuid.uuid4(), {"upload_path": name})
    try:
        checks = load_builtin_checks().for_target(TargetType.APK, 1)
        result = await run_checks(ctx, checks)
    finally:
        await ctx.aclose()

    assert result.failed == []
    assert {f.check_id for f in result.findings} == EXPECTED[fixture]
    score = compute_score(result.findings, {"security": 0.7, "prod_readiness": 0.3})
    if any(f.severity == Severity.CRITICAL for f in result.findings):
        assert score.overall <= 49  # "yayına hazır değil" eşiği
    # Hiçbir kanıtta tam secret yok (fixture'daki anahtarlar uzun ve maskeli olmalı)
    for finding in result.findings:
        for value in json.dumps(finding.evidence).split('"'):
            assert not (value.startswith(("sk-proj-", "sk_live_")) and len(value) > 20)
    assert list(upload.iterdir()) == [] and list(work.iterdir()) == []
