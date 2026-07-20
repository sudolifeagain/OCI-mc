import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass


SHELL_TIMEOUT_SECONDS = 60
SHELL_MAX_OUTPUT_BYTES = 64 * 1024
SHELL_ENV_KEYS = {
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "TERM",
    "TZ",
    "USER",
}


@dataclass(frozen=True)
class ShellResult:
    returncode: int | None
    output: str
    timed_out: bool = False
    output_limited: bool = False


async def run_shell_command(command: str) -> ShellResult:
    """任意シェルコマンドを時間・出力量制限付きで実行する。"""
    started = time.monotonic()
    command_digest = hashlib.sha256(command.encode("utf-8")).hexdigest()[:12]
    logging.info(
        "Shell command started: digest=%s length=%d",
        command_digest,
        len(command),
    )
    shell_env = {key: value for key, value in os.environ.items() if key in SHELL_ENV_KEYS}
    shell_env.setdefault(
        "PATH",
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    )

    proc = await asyncio.create_subprocess_exec(
        "/usr/bin/timeout",
        "--signal=TERM",
        "--kill-after=5s",
        f"{SHELL_TIMEOUT_SECONDS}s",
        "/bin/bash",
        "-lc",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd="/opt/minecraft",
        env=shell_env,
    )

    output = bytearray()
    output_limited = False
    assert proc.stdout is not None
    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        remaining = SHELL_MAX_OUTPUT_BYTES - len(output)
        if remaining <= 0:
            output_limited = True
            proc.terminate()
            break
        output.extend(chunk[:remaining])
        if len(chunk) > remaining:
            output_limited = True
            proc.terminate()
            break

    try:
        returncode = await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        proc.kill()
        returncode = await proc.wait()

    elapsed = time.monotonic() - started
    timed_out = returncode in {124, 137}
    logging.info(
        "Shell command finished: digest=%s returncode=%s duration=%.2fs "
        "timed_out=%s output_limited=%s",
        command_digest,
        returncode,
        elapsed,
        timed_out,
        output_limited,
    )
    return ShellResult(
        returncode=returncode,
        output=output.decode("utf-8", errors="replace"),
        timed_out=timed_out,
        output_limited=output_limited,
    )
