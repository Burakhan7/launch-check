"""Lighthouse CLI (Node, worker-deep imajında) çalıştırma ve rapor ayrıştırma.

Güvenlik (PHASE2 §12.1): Chromium'u biz başlatırız, Lighthouse `--port` ile ona bağlanır.
Böylece Chromium bayrakları (egress proxy, ad çözümleme yasağı, QUIC/WebRTC kapalı) render
modülündekiyle birebir aynıdır ve Lighthouse'un `--chrome-flags` ayrıştırmasına bağlı değildir.
Her ölçüm yeni ve boş bir profil + geçici klasörde çalışır; süreçler `finally` ile öldürülür.
Alt süreçlere gizli ortam değişkeni (API anahtarı, DB adresi) geçirilmez.

Rapor JSON'u sadece bellekte işlenir; sayfa içeriği/ekran görüntüleri loglanmaz.
"""

import asyncio
import contextlib
import glob
import json
import logging
import os
import re
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from launchcheck.checks.shared.domains import registered_domain
from launchcheck.config import Settings
from launchcheck.deep.browser.launcher import browser_user_agent, chromium_args
from launchcheck.deep.perf.filmstrip import blank_until, decode_frames
from launchcheck.deep.perf_data import (
    DESKTOP,
    CacheItem,
    ImageItem,
    LighthouseRun,
    Opportunity,
    ResourceItem,
    ThirdParty,
    UncompressedItem,
)

logger = logging.getLogger(__name__)

CATEGORIES = "performance,best-practices,seo"
# Ölçüm kararlılığı için (chrome-launcher'ın varsayılanlarından)
STABILITY_FLAGS = [
    "--headless",
    "--disable-extensions",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-client-side-phishing-detection",
    "--disable-default-apps",
    "--disable-hang-monitor",
    "--mute-audio",
    "--metrics-recording-only",
    "--password-store=basic",
    "--use-mock-keychain",
]
PORT_FILE = "DevToolsActivePort"
CHROME_START_TIMEOUT_S = 15
MAX_WORKDIR_CHARS = 48
COMPRESSIBLE_RE = re.compile(r"^text/|javascript|json|xml|svg|ecmascript", re.IGNORECASE)
COMPRESSIBLE_TYPES = {"Document", "Script", "Stylesheet", "Fetch", "XHR"}
UNCOMPRESSED_RATIO = 0.9  # aktarılan ≥ %90 × gerçek boyut → sıkıştırılmamış
MIN_COMPRESSIBLE_BYTES = 1024
DISPLAYED_RE = re.compile(r"\((\d+)x(\d+)\)[^()]*\((\d+)x(\d+)\)")
MAX_ITEMS = 10
MAX_OPPORTUNITIES = 5
# LCP önerisi sayılmayanlar: metriğin kendisi/dökümü ve ilk ziyarette etkisi olmayan önbellek
LCP_SKIP = {"largest-contentful-paint", "lcp-breakdown-insight", "metrics", "cache-insight"}


class LighthouseError(Exception):
    """Ölçüm kullanılamadı. `code`: timeout, runtime:<LH kodu>, http_status, no_report..."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- ayrıştırma (saf fonksiyonlar)


def strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))[:300]


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _audit(report: dict[str, Any], audit_id: str) -> dict[str, Any]:
    return (report.get("audits") or {}).get(audit_id) or {}


def _items(report: dict[str, Any], audit_id: str) -> list[dict[str, Any]]:
    items = (_audit(report, audit_id).get("details") or {}).get("items") or []
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _savings(audit: dict[str, Any], metric: str) -> float:
    return _num((audit.get("metricSavings") or {}).get(metric)) or 0.0


def _main_status(report: dict[str, Any]) -> int | None:
    final = report.get("finalDisplayedUrl") or report.get("mainDocumentUrl")
    documents = [
        i for i in _items(report, "network-requests") if i.get("resourceType") == "Document"
    ]
    if not documents:
        return None
    main = next((i for i in documents if i.get("url") == final), documents[-1])
    return int(main.get("statusCode") or 0) or None


def _image_reasons(
    item: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[int, int] | None, tuple[int, int] | None]:
    reasons: list[str] = []
    natural = displayed = None
    for sub in ((item.get("subItems") or {}).get("items")) or []:
        text = str(sub.get("reason") or "")
        lower = text.lower()
        if "modern image format" in lower:
            reasons.append("format")
        elif "larger than it needs" in lower or "displayed dimensions" in lower:
            reasons.append("oversized")
            match = DISPLAYED_RE.search(text)
            if match:
                a, b, c, d = (int(g) for g in match.groups())
                natural, displayed = (a, b), (c, d)
        elif "compression" in lower:
            reasons.append("compression")
        elif "video" in lower or "animated" in lower:
            reasons.append("animated")
    return tuple(dict.fromkeys(reasons)), natural, displayed


def _uncompressed(report: dict[str, Any]) -> list[UncompressedItem]:
    out = []
    for item in _items(report, "network-requests"):
        url = str(item.get("url") or "")
        resource = int(item.get("resourceSize") or 0)
        transfer = int(item.get("transferSize") or 0)
        compressible = item.get("resourceType") in COMPRESSIBLE_TYPES or COMPRESSIBLE_RE.search(
            str(item.get("mimeType") or "")
        )
        if (
            url.startswith(("http://", "https://"))
            and item.get("statusCode") == 200
            and compressible
            and resource >= MIN_COMPRESSIBLE_BYTES
            and transfer >= resource * UNCOMPRESSED_RATIO
        ):
            out.append(UncompressedItem(strip_query(url), resource, transfer))
    return sorted(out, key=lambda i: -i.resource_bytes)[:MAX_ITEMS]


def _opportunities(report: dict[str, Any]) -> list[Opportunity]:
    found = []
    for audit_id, audit in (report.get("audits") or {}).items():
        if audit_id in LCP_SKIP or not isinstance(audit, dict):
            continue
        score = _num(audit.get("score"))
        saving = _savings(audit, "LCP")
        if saving > 0 and (score is None or score < 0.9):
            found.append(Opportunity(audit_id, round(saving)))
    return sorted(found, key=lambda o: -o.savings_ms)[:MAX_OPPORTUNITIES]


def parse_report(
    report: dict[str, Any], url: str, form_factor: str, blank_min_ratio: float
) -> LighthouseRun:
    runtime = report.get("runtimeError")
    if isinstance(runtime, dict) and runtime.get("code"):
        raise LighthouseError(f"runtime:{runtime['code']}")
    status = _main_status(report)
    if status is not None and status >= 400:
        raise LighthouseError("http_status")  # hata sayfası veya bot doğrulaması ölçülmez
    final = str(report.get("finalDisplayedUrl") or url)
    home_domain = registered_domain(urlsplit(final).hostname or "")

    def third_party(u: str) -> bool:
        return registered_domain(urlsplit(u).hostname or "") != home_domain

    run = LighthouseRun(
        url=url,
        form_factor=form_factor,
        final_url=final,
        lcp_ms=_num(_audit(report, "largest-contentful-paint").get("numericValue")),
        cls=_num(_audit(report, "cumulative-layout-shift").get("numericValue")),
        fcp_ms=_num(_audit(report, "first-contentful-paint").get("numericValue")),
        speed_index_ms=_num(_audit(report, "speed-index").get("numericValue")),
        tbt_ms=_num(_audit(report, "total-blocking-time").get("numericValue")),
        scores={
            k: float(v["score"])
            for k, v in (report.get("categories") or {}).items()
            if isinstance(v, dict) and _num(v.get("score")) is not None
        },
    )
    # Kullanılmayan JS (Lighthouse coverage)
    unused = _audit(report, "unused-javascript")
    run.unused_js = [
        ResourceItem(
            strip_query(str(i.get("url") or "")),
            int(i.get("totalBytes") or 0),
            int(i.get("wastedBytes") or 0),
            third_party(str(i.get("url") or "")),
        )
        for i in _items(report, "unused-javascript")
    ][:MAX_ITEMS]
    run.unused_js_bytes = int((unused.get("details") or {}).get("overallSavingsBytes") or 0) or sum(
        i.wasted_bytes for i in run.unused_js
    )
    run.unused_js_savings_ms = _savings(unused, "LCP") or _savings(unused, "FCP")
    run.total_js_bytes = next(
        (
            int(i.get("transferSize") or 0)
            for i in _items(report, "resource-summary")
            if i.get("resourceType") == "script"
        ),
        0,
    )
    # Görseller (Lighthouse 13: image-delivery-insight) ve boyutsuz görseller
    images = _audit(report, "image-delivery-insight")
    for item in _items(report, "image-delivery-insight"):
        reasons, natural, displayed = _image_reasons(item)
        run.images.append(
            ImageItem(
                strip_query(str(item.get("url") or "")),
                int(item.get("totalBytes") or 0),
                int(item.get("wastedBytes") or 0),
                reasons,
                natural,
                displayed,
            )
        )
    run.images = sorted(run.images, key=lambda i: -i.wasted_bytes)[:MAX_ITEMS]
    run.image_savings_bytes = int(_num(images.get("numericValue")) or 0) or sum(
        i.wasted_bytes for i in run.images
    )
    run.image_savings_ms = _savings(images, "LCP")
    run.unsized_images = list(
        dict.fromkeys(
            strip_query(str(i.get("url") or "")) for i in _items(report, "unsized-images")
        )
    )[:MAX_ITEMS]
    # Sıkıştırma ve önbellek
    run.uncompressed = _uncompressed(report)
    cache = _audit(report, "cache-insight")
    run.short_cache = [
        CacheItem(
            strip_query(str(i.get("url") or "")),
            round((_num(i.get("cacheLifetimeMs")) or 0) / 1000),
            int(i.get("totalBytes") or 0),
        )
        for i in _items(report, "cache-insight")
        if not third_party(str(i.get("url") or ""))
    ][:MAX_ITEMS]
    run.cache_savings_ms = _savings(cache, "LCP")
    # Üçüncü taraflar (Lighthouse varlık gruplaması; birinci taraf listede yer almaz)
    run.third_parties = sorted(
        (
            ThirdParty(
                str(i.get("entity") or "")[:100],
                int(i.get("transferSize") or 0),
                round(_num(i.get("mainThreadTime")) or 0),
            )
            for i in _items(report, "third-parties-insight")
            if i.get("entity") and registered_domain(str(i.get("entity"))) != home_domain
        ),
        key=lambda t: -t.transfer_bytes,
    )[:MAX_ITEMS]
    run.lcp_opportunities = _opportunities(report)
    # Filmstrip (gözlenen, kısıtlamasız zaman çizelgesi; bkz. filmstrip.py)
    run.frames = decode_frames(_items(report, "screenshot-thumbnails"))
    run.blank_ms = blank_until(run.frames, run.fcp_ms, blank_min_ratio)
    return run


# --- çalıştırma


@dataclass(frozen=True)
class LighthouseConfig:
    lighthouse_bin: str
    chrome_path: str
    run_timeout_s: float
    max_wait_for_load_ms: int
    max_report_bytes: int
    tmp_dir: str
    bot_user_agent: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "LighthouseConfig":
        return cls(
            lighthouse_bin=settings.deep_lighthouse_bin,
            chrome_path=settings.deep_chrome_path or find_chromium(),
            run_timeout_s=settings.deep_lighthouse_run_timeout_s,
            max_wait_for_load_ms=settings.deep_lighthouse_max_wait_for_load_ms,
            max_report_bytes=settings.deep_lighthouse_max_report_bytes,
            tmp_dir=settings.scan_tmp_dir,
            bot_user_agent=settings.bot_user_agent,
        )


def find_chromium() -> str:
    """Playwright'ın kurduğu Chromium (worker-deep imajı, PLAYWRIGHT_BROWSERS_PATH)."""
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")
    found = sorted(glob.glob(os.path.join(base, "chromium-*", "chrome-linux*", "chrome")))
    return found[-1] if found else "chromium"


def lighthouse_args(
    cfg: LighthouseConfig, url: str, form_factor: str, port: int, out: str
) -> list[str]:
    args = [
        cfg.lighthouse_bin,
        url,
        f"--port={port}",
        "--hostname=127.0.0.1",
        "--output=json",
        f"--output-path={out}",
        "--quiet",
        "--locale=en-US",
        f"--only-categories={CATEGORIES}",
        f"--max-wait-for-load={cfg.max_wait_for_load_ms}",
        "--disable-full-page-screenshot",
        f"--emulated-user-agent={browser_user_agent(cfg.bot_user_agent, form_factor != DESKTOP)}",
    ]
    args.append("--preset=desktop" if form_factor == DESKTOP else "--form-factor=mobile")
    return args


def _child_env(workdir: str) -> dict[str, str]:
    """Sadece gerekli değişkenler: gizli ayarlar (LC_*) alt süreçlere geçmez."""
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": workdir,
        "TMPDIR": workdir,
        "XDG_CONFIG_HOME": workdir,
        "XDG_CACHE_HOME": workdir,
        "LANG": "C.UTF-8",
    }


async def _kill(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None or proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)  # tüm süreç grubu (Chromium alt süreçleri dahil)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), 5)


async def _wait_port(profile: Path, proc: asyncio.subprocess.Process) -> int:
    deadline = time.monotonic() + CHROME_START_TIMEOUT_S
    port_file = profile / PORT_FILE
    while time.monotonic() < deadline:
        if proc.returncode is not None:
            raise LighthouseError("chrome_exited")
        with contextlib.suppress(OSError, ValueError, IndexError):
            return int(port_file.read_text().splitlines()[0])
        await asyncio.sleep(0.1)
    raise LighthouseError("chrome_start_timeout")


class LighthouseRunner:
    """Tek ölçüm: yeni Chromium (egress proxy'ye bağlı) + Lighthouse. Rapor JSON'unu döner."""

    def __init__(self, cfg: LighthouseConfig, proxy_port: int) -> None:
        self._cfg = cfg
        self._proxy_port = proxy_port

    async def run(
        self, url: str, form_factor: str, timeout_s: float | None = None
    ) -> dict[str, Any]:
        cfg = self._cfg
        os.makedirs(cfg.tmp_dir, exist_ok=True)
        workdir = tempfile.mkdtemp(prefix="lh-", dir=cfg.tmp_dir)
        if len(workdir) > MAX_WORKDIR_CHARS:
            # Chromium profil klasöründe Unix soketi açar (yol sınırı 108 karakter)
            shutil.rmtree(workdir, ignore_errors=True)
            raise LighthouseError("tmp_dir_too_long")
        chrome: asyncio.subprocess.Process | None = None
        lighthouse: asyncio.subprocess.Process | None = None
        try:
            async with asyncio.timeout(min(timeout_s or cfg.run_timeout_s, cfg.run_timeout_s)):
                profile = Path(workdir) / "profile"
                chrome = await asyncio.create_subprocess_exec(
                    cfg.chrome_path,
                    *chromium_args(self._proxy_port),
                    *STABILITY_FLAGS,
                    f"--user-data-dir={profile}",
                    "--remote-debugging-port=0",
                    "--remote-debugging-address=127.0.0.1",
                    "about:blank",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=_child_env(workdir),
                    start_new_session=True,
                )
                port = await _wait_port(profile, chrome)
                out = os.path.join(workdir, "report.json")
                lighthouse = await asyncio.create_subprocess_exec(
                    *lighthouse_args(cfg, url, form_factor, port, out),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=_child_env(workdir),
                    cwd=workdir,
                    start_new_session=True,
                )
                await lighthouse.wait()
            return self._read(out)
        except TimeoutError as exc:
            raise LighthouseError("timeout") from exc
        finally:
            await _kill(lighthouse)
            await _kill(chrome)
            shutil.rmtree(workdir, ignore_errors=True)

    def _read(self, path: str) -> dict[str, Any]:
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            raise LighthouseError("no_report") from exc
        if size > self._cfg.max_report_bytes:
            raise LighthouseError("report_too_large")
        try:
            with open(path, "rb") as fh:
                report = json.load(fh)
        except (OSError, ValueError) as exc:
            raise LighthouseError("bad_report") from exc
        if not isinstance(report, dict):
            raise LighthouseError("bad_report")
        return report
