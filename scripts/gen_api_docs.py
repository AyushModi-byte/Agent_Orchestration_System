"""Regenerate docs/api.md and web/openapi.json from the FastAPI app (`make docs`).

Uses the demo app, which mounts every router, so no database or provider is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def ref_name(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "-"
    if "$ref" in schema:
        return f"`{schema['$ref'].rsplit('/', 1)[-1]}`"
    if schema.get("type") == "array":
        return f"list of {ref_name(schema.get('items'))}"
    return f"`{schema.get('type', 'object')}`"


def render(spec: dict[str, Any]) -> str:
    lines = [
        "# API",
        "",
        "Generated from the FastAPI OpenAPI schema by `make docs`; do not edit by hand.",
        "Every body is a Pydantic v2 model. The React client in `web/` is generated from the same",
        "schema (`npm run api:types`). Interactive docs: `/docs` on the running API.",
        "",
        "| Method | Path | Request body | Response | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            body = (op.get("requestBody") or {}).get("content", {}).get("application/json", {})
            ok = next((c for c in op["responses"] if c.startswith("2")), "200")
            resp = op["responses"][ok].get("content", {})
            kind = next(iter(resp), "")
            out = ref_name(resp.get("application/json", {}).get("schema")) if resp else "-"
            if kind == "text/event-stream" or path.endswith("/events"):
                out = "server-sent events"
            params = [p["name"] for p in op.get("parameters", []) if p["in"] == "query"]
            notes = f"query: {', '.join(params)}; " if params else ""
            notes += f"returns {ok}"
            req = ref_name(body.get("schema"))
            lines.append(f"| {method.upper()} | `{path}` | {req} | {out} | {notes} |")
    lines += ["", "## Models", ""]
    for name, schema in sorted(spec["components"]["schemas"].items()):
        if name in ("HTTPValidationError", "ValidationError"):
            continue
        props = ", ".join(
            f"`{k}`{'' if k in schema.get('required', []) else '?'}"
            for k in schema.get("properties", {})
        )
        lines.append(f"- **{name}**: {props}")
    return "\n".join(lines) + "\n"


def main() -> None:
    from demo.runtime import build_demo_app

    spec = build_demo_app().openapi()
    (ROOT / "web" / "openapi.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    (ROOT / "docs" / "api.md").write_text(render(spec), encoding="utf-8")
    print(f"wrote docs/api.md ({len(spec['paths'])} paths)")


if __name__ == "__main__":
    main()
