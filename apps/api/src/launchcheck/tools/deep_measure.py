"""P11 ölçüm aracı: verilen sitelerde uçtan uca Faz 1 + Faz 2 taraması, süre ve bellek ölçümü.

Çalışan bir API'ye (varsayılan http://localhost:8000) normal kullanıcı gibi istek atar; taramaları
worker'lar yapar. İsteğe bağlı olarak worker-deep container'ının bellek/CPU tepe değerini
`docker stats` ile örnekler. Sonuç: site başına JSON satırları + p50/p90 özet tablosu (Markdown).

Kullanım (sadece sahibi olunan veya taranmasına izin verilen sitelerle):
    python -m launchcheck.tools.deep_measure --out p11.jsonl \\
        --container viber-worker-deep-1 nextjs=https://... wordpress=https://...

Ölçüm ortamında derin tarama günlük sınırı yükseltilmelidir (LC_RATE_LIMIT_DEEP_PER_DAY).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

POLL_S = 3.0
PHASE1_TIMEOUT_S = 300.0
# Sert sınır 20 dk + kuyruk ve son yazma payı
DEEP_TIMEOUT_S = 1500.0
STATS_EVERY_S = 2.0
TARGET_P90_S = 15 * 60
HARD_LIMIT_S = 20 * 60

_UNITS = {
    "b": 1,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
}


@dataclass
class SiteResult:
    label: str
    url: str
    status: str = "pending"
    error: str | None = None
    phase1_s: float | None = None
    deep_wall_s: float | None = None
    deep_duration_s: float | None = None
    module_durations: dict[str, float] = field(default_factory=dict)
    partial_modules: list[str] = field(default_factory=list)
    failed_modules: list[str] = field(default_factory=list)
    findings: int = 0
    peak_mem_bytes: int | None = None
    peak_swap_bytes: int | None = None
    peak_cpu_pct: float | None = None


def percentile(values: Sequence[float], q: float) -> float | None:
    """En yakın sıra yöntemi (küçük örneklemde enterpolasyonsuz, gözlenmiş bir değer döner)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def parse_mem(text: str) -> int | None:
    """`docker stats` MemUsage alanının ilk kısmı: "1.234GiB / 6GiB" → bayt."""
    match = re.match(r"\s*([\d.]+)\s*([KMG]?i?B)", text, re.IGNORECASE)
    if not match:
        return None
    return int(float(match.group(1)) * _UNITS[match.group(2).lower()])


def parse_swap(text: str) -> int | None:
    """cgroup v2 `memory.swap.current` (bayt). Dosya yoksa (cgroup v1, swap kapalı) None."""
    text = text.strip()
    return int(text) if text.isdigit() else None


def parse_cpu(text: str) -> float | None:
    match = re.match(r"\s*([\d.]+)%", text)
    return float(match.group(1)) if match else None


def summarize(results: Sequence[SiteResult]) -> dict[str, Any]:
    done = [r for r in results if r.status == "done" and r.deep_duration_s is not None]
    totals = [r.deep_duration_s for r in done if r.deep_duration_s is not None]
    modules = sorted({m for r in done for m in r.module_durations})
    per_module = {
        m: {
            "p50": percentile([r.module_durations[m] for r in done if m in r.module_durations], 50),
            "p90": percentile([r.module_durations[m] for r in done if m in r.module_durations], 90),
            "max": max(r.module_durations.get(m, 0.0) for r in done),
            "partial": sum(m in r.partial_modules for r in done),
            "failed": sum(m in r.failed_modules for r in done),
        }
        for m in modules
    }
    mems = [r.peak_mem_bytes for r in done if r.peak_mem_bytes is not None]
    swaps = [r.peak_swap_bytes for r in done if r.peak_swap_bytes is not None]
    p90 = percentile(totals, 90)
    return {
        "sites": len(results),
        "done": len(done),
        "total_p50_s": percentile(totals, 50),
        "total_p90_s": p90,
        "total_max_s": max(totals) if totals else None,
        "meets_p90_target": p90 is not None and p90 <= TARGET_P90_S,
        "over_hard_limit": [r.label for r in done if (r.deep_duration_s or 0) > HARD_LIMIT_S],
        "peak_mem_bytes": max(mems) if mems else None,
        "peak_swap_bytes": max(swaps) if swaps else None,
        "modules": per_module,
    }


def _fmt_s(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value / 60:.1f} dk" if value >= 90 else f"{value:.0f} sn"


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value / 1024**3:.2f} GiB" if value >= 1024**3 else f"{value / 1024**2:.0f} MiB"


def markdown(results: Sequence[SiteResult], summary: dict[str, Any]) -> str:
    lines = [
        "| Site | Durum | Faz 1 | Faz 2 | Kısmi | Başarısız | Bellek tepe | Swap tepe |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        mem = _fmt_bytes(r.peak_mem_bytes)
        swap = _fmt_bytes(r.peak_swap_bytes)
        lines.append(
            f"| {r.label} | {r.status} | {_fmt_s(r.phase1_s)} | {_fmt_s(r.deep_duration_s)} "
            f"| {', '.join(r.partial_modules) or '-'} | {', '.join(r.failed_modules) or '-'} "
            f"| {mem} | {swap} |"
        )
    lines += [
        "",
        f"Toplam (Faz 2): p50 {_fmt_s(summary['total_p50_s'])}, "
        f"p90 {_fmt_s(summary['total_p90_s'])}, en uzun {_fmt_s(summary['total_max_s'])}. "
        "p90 ≤ 15 dk: "
        f"{'evet' if summary['meets_p90_target'] else 'hayır'}.",
        "",
        "| Modül | p50 | p90 | En uzun | Bütçeyi aşan | Başarısız |",
        "|---|---|---|---|---|---|",
    ]
    for name, m in summary["modules"].items():
        lines.append(
            f"| {name} | {_fmt_s(m['p50'])} | {_fmt_s(m['p90'])} | {_fmt_s(m['max'])} "
            f"| {m['partial']} | {m['failed']} |"
        )
    return "\n".join(lines) + "\n"


class StatsSampler:
    """worker-deep container'ının bellek/CPU tepe değerini arka planda örnekler."""

    def __init__(self, container: str | None) -> None:
        self._container = container
        self._task: asyncio.Task[None] | None = None
        self.peak_mem: int | None = None
        self.peak_cpu: float | None = None
        self.peak_swap: int | None = None

    async def _swap_now(self) -> int | None:
        assert self._container
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            self._container,
            "cat",
            "/sys/fs/cgroup/memory.swap.current",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return parse_swap(out.decode())

    async def _sample(self) -> None:
        assert self._container
        while True:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.MemUsage}}|{{.CPUPerc}}",
                self._container,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            mem_text, _, cpu_text = out.decode().strip().partition("|")
            mem, cpu = parse_mem(mem_text), parse_cpu(cpu_text)
            if mem is not None:
                self.peak_mem = max(self.peak_mem or 0, mem)
            if cpu is not None:
                self.peak_cpu = max(self.peak_cpu or 0.0, cpu)
            swap = await self._swap_now()
            if swap is not None:
                self.peak_swap = max(self.peak_swap or 0, swap)
            await asyncio.sleep(STATS_EVERY_S)

    def __enter__(self) -> StatsSampler:
        if self._container:
            self._task = asyncio.get_running_loop().create_task(self._sample())
        return self

    def __exit__(self, *_: object) -> None:
        if self._task:
            self._task.cancel()


def _error_code(report: dict[str, Any]) -> str | None:
    error = report.get("error")
    return error.get("code") if isinstance(error, dict) else None


async def _poll(client: httpx.AsyncClient, scan_id: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        data: dict[str, Any] = (await client.get(f"/api/scans/{scan_id}")).json()
        if data.get("status") in {"done", "failed"}:
            return data
        await asyncio.sleep(POLL_S)
    raise TimeoutError(scan_id)


async def measure_site(
    client: httpx.AsyncClient, label: str, url: str, container: str | None
) -> SiteResult:
    result = SiteResult(label=label, url=url)
    try:
        started = time.monotonic()
        created = await client.post("/api/scans/web", json={"url": url})
        created.raise_for_status()
        parent_id = created.json()["scan_id"]
        parent = await _poll(client, parent_id, PHASE1_TIMEOUT_S)
        result.phase1_s = round(time.monotonic() - started, 1)
        if parent["status"] != "done":
            result.status, result.error = "phase1_failed", _error_code(parent)
            return result

        with StatsSampler(container) as sampler:
            started = time.monotonic()
            deep = await client.post(f"/api/scans/{parent_id}/deep", json={"use_reference": True})
            if deep.status_code != 202:
                result.status, result.error = "deep_rejected", deep.text[:200]
                return result
            report = await _poll(client, deep.json()["scan_id"], DEEP_TIMEOUT_S)
            result.deep_wall_s = round(time.monotonic() - started, 1)
        result.peak_mem_bytes, result.peak_cpu_pct = sampler.peak_mem, sampler.peak_cpu
        result.peak_swap_bytes = sampler.peak_swap

        meta = (report.get("meta") or {}).get("deep") or {}
        result.status = report["status"]
        result.error = _error_code(report)
        result.deep_duration_s = meta.get("duration_s")
        result.module_durations = meta.get("module_durations") or {}
        result.partial_modules = meta.get("partial_modules") or []
        result.failed_modules = meta.get("failed_modules") or []
        result.findings = len(report.get("findings") or [])
    except (httpx.HTTPError, TimeoutError, KeyError, ValueError) as exc:
        result.status, result.error = "error", type(exc).__name__
    return result


def _parse_targets(items: Sequence[str]) -> list[tuple[str, str]]:
    targets = []
    for item in items:
        label, sep, url = item.partition("=")
        targets.append((label, url) if sep else (item, item))
    return targets


def _append_line(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


async def run(args: argparse.Namespace) -> None:
    out = Path(args.out)
    results: list[SiteResult] = []
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as client:
        for label, url in _parse_targets(args.targets):
            result = await measure_site(client, label, url, args.container)
            results.append(result)
            # Site başına hemen yazılır: uzun ölçüm yarıda kesilirse biten siteler kaybolmaz
            await asyncio.to_thread(_append_line, out, json.dumps(asdict(result)))
            print(f"{label}: {result.status} {_fmt_s(result.deep_duration_s)}", flush=True)
    summary = summarize(results)
    await asyncio.to_thread(
        out.with_suffix(".md").write_text, markdown(results, summary), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("targets", nargs="+", help="etiket=https://... (sıralı taranır)")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--container", help="bellek örneklemesi için worker-deep container adı")
    parser.add_argument("--out", default="p11.jsonl")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
