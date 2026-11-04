"""Dependency audit via OSV. Runs on the host (the sandbox has no network by design);
it only sends package names and versions, never target-repo code."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import httpx
from pydantic import BaseModel

from conductor.tools.models import Finding
from conductor.tools.registry import tool

_PIN = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([A-Za-z0-9_.\-+!]+)")


class AuditInput(BaseModel):
    requirements_path: str


class AuditOutput(BaseModel):
    findings: list[Finding]
    checked: int


def parse_requirements(text: str) -> list[tuple[str, str, int]]:
    """Only exact pins can be matched against OSV; ranges are skipped."""
    out = []
    for n, line in enumerate(text.splitlines(), start=1):
        if m := _PIN.match(line.split("#", 1)[0]):
            out.append((m.group(1), m.group(2), n))
    return out


async def audit_requirements(path: str) -> AuditOutput:
    text = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
    pins = parse_requirements(text)
    if not pins:
        return AuditOutput(findings=[], checked=0)
    queries = [
        {"package": {"name": name, "ecosystem": "PyPI"}, "version": ver} for name, ver, _ in pins
    ]
    base = os.environ.get("OSV_API_URL", "https://api.osv.dev")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{base}/v1/querybatch", json={"queries": queries})
        r.raise_for_status()
        results = r.json()["results"]
    findings = [
        Finding(
            file=Path(path).name,
            line=line,
            rule=v["id"],
            severity="medium",  # querybatch carries no severity; details need a second call
            message=f"{name}=={ver} has known vulnerability {v['id']}",
        )
        for (name, ver, line), res in zip(pins, results, strict=True)
        for v in res.get("vulns", [])
    ]
    return AuditOutput(findings=findings, checked=len(pins))


@tool(
    name="audit_requirements",
    description="Check pinned requirements against the OSV vulnerability database.",
    input_schema=AuditInput,
    output_schema=AuditOutput,
    allowed_agents={"security", "reviewer", "mcp"},
    rate_limit=10,
)
async def _audit_tool(args: AuditInput) -> AuditOutput:
    return await audit_requirements(args.requirements_path)
