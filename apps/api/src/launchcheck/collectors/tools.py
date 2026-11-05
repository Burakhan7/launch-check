"""Harici araç (apktool, jadx) çalıştırıcı.

- `shell=False` (create_subprocess_exec), en az ortam değişkeni, çalışma dizini geçici klasör
- Zaman aşımında veya iptalde (tarama timeout'u) tüm süreç grubu öldürülür
- stdout/stderr atılır: araç çıktısı APK içeriği taşıyabilir, loglanmaz
"""

import asyncio
import contextlib
import os
import signal
from pathlib import Path


class ToolError(Exception):
    pass


class ToolTimeout(ToolError):
    pass


def tool_env(work_dir: Path, java_max_heap: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(work_dir),
        "TMPDIR": str(work_dir),
        "LANG": "C.UTF-8",
        # Java HOME yerine user.home'a bakar; salt okunur root FS'te tüm yazımlar work_dir'e.
        "JAVA_TOOL_OPTIONS": (
            f"-Djava.io.tmpdir={work_dir} -Duser.home={work_dir} -Xmx{java_max_heap}"
        ),
        "JADX_CONFIG_DIR": str(work_dir / ".jadx-config"),
        "JADX_CACHE_DIR": str(work_dir / ".jadx-cache"),
    }


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:  # pragma: no cover - sadece Linux container'ında çalışır
            proc.kill()


async def run_tool(args: list[str], cwd: Path, env: dict[str, str], timeout_s: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=os.name == "posix",
    )
    try:
        returncode = await asyncio.wait_for(proc.wait(), timeout_s)
    except TimeoutError as exc:
        _kill(proc)
        await proc.wait()
        raise ToolTimeout(args[0]) from exc
    except BaseException:  # iptal (tarama zaman aşımı) dahil: süreç arkada kalmasın
        _kill(proc)
        with contextlib.suppress(Exception):
            await asyncio.shield(proc.wait())
        raise
    if returncode != 0:
        raise ToolError(f"{args[0]} çıkış kodu {returncode}")
