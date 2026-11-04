"""Real-container tests. Need a Docker daemon and the conductor-sandbox image
(docker build -t conductor-sandbox:latest sandbox/)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conductor.tools.sandbox import IMAGE, run_in_sandbox

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module", autouse=True)
def _require_docker() -> None:
    try:
        import docker

        docker.from_env().images.get(IMAGE)
    except Exception as exc:
        pytest.skip(f"docker or {IMAGE} unavailable: {exc}")


async def test_network_blocked(tmp_path: Path) -> None:
    code = "import socket;socket.create_connection(('1.1.1.1',80),timeout=3)"
    res = await run_in_sandbox(tmp_path, ["python", "-c", code], timeout=30)
    assert res.exit_code != 0


async def test_timeout_kills(tmp_path: Path) -> None:
    res = await run_in_sandbox(tmp_path, ["sleep", "60"], timeout=2)
    assert res.timed_out


async def test_rootfs_and_mount_are_read_only(tmp_path: Path) -> None:
    for target in ("/etc/x", "/work/x"):
        res = await run_in_sandbox(tmp_path, ["sh", "-c", f"echo hi > {target}"], timeout=30)
        assert res.exit_code != 0
    ok = await run_in_sandbox(tmp_path, ["sh", "-c", "echo hi > /tmp/x"], timeout=30)
    assert ok.exit_code == 0


async def test_runs_as_non_root_and_truncates(tmp_path: Path) -> None:
    res = await run_in_sandbox(tmp_path, ["id", "-u"], timeout=30)
    assert res.stdout.strip() == "10001"
    big = await run_in_sandbox(tmp_path, ["python", "-c", "print('a'*500000)"], timeout=30)
    assert big.truncated and len(big.stdout) < 250_000
