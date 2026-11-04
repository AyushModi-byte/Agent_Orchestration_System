"""Run untrusted target-repo commands in a locked-down container. Never on the host."""

from __future__ import annotations

import asyncio
from pathlib import Path

import docker
from pydantic import BaseModel

IMAGE = "conductor-sandbox:latest"
MAX_OUTPUT = 200_000


class SandboxResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


def _truncate(data: bytes, limit: int) -> tuple[str, bool]:
    """Keep head and tail: summaries (e.g. the junit footer) are printed last."""
    text = data.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:], True


def _run_blocking(snapshot_dir: Path, cmd: list[str], timeout: int, image: str) -> SandboxResult:
    client = docker.from_env()
    container = client.containers.run(
        image,
        cmd,
        detach=True,
        network_mode="none",
        read_only=True,
        tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},  # noqa: S108
        mem_limit="256m",
        memswap_limit="256m",
        nano_cpus=500_000_000,
        pids_limit=128,
        user="sandbox",
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        volumes={str(snapshot_dir.resolve()): {"bind": "/work", "mode": "ro"}},
        working_dir="/work",
    )
    timed_out = False
    try:
        try:
            exit_code = int(container.wait(timeout=timeout)["StatusCode"])
        except Exception:  # requests timeout / connection error from the docker SDK
            timed_out = True
            container.kill()
            exit_code = 137
        out, t1 = _truncate(container.logs(stdout=True, stderr=False), MAX_OUTPUT)
        err, t2 = _truncate(container.logs(stdout=False, stderr=True), MAX_OUTPUT)
    finally:
        container.remove(force=True)
    return SandboxResult(
        exit_code=exit_code, stdout=out, stderr=err, timed_out=timed_out, truncated=t1 or t2
    )


async def run_in_sandbox(
    snapshot_dir: str | Path, cmd: list[str], timeout: int = 60, image: str = IMAGE
) -> SandboxResult:
    """Execute cmd against a read-only mount of snapshot_dir; kill hard after timeout seconds."""
    return await asyncio.to_thread(_run_blocking, Path(snapshot_dir), cmd, timeout, image)
