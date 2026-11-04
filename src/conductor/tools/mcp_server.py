"""Serve registry tools over MCP. One command: python -m conductor.tools.mcp_server

Calls run as the "mcp" agent, so only tools that list "mcp" in allowed_agents are exposed.
Write tools (post_review_comment, push_fix_commit) never list it.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from conductor.tools import deps, github, linters  # noqa: F401  (import registers tools)
from conductor.tools.registry import ToolRegistry, default_registry

MCP_AGENT = "mcp"


def build_server(registry: ToolRegistry = default_registry) -> FastMCP:
    server = FastMCP("conductor-tools")
    for t in registry.tools():
        if MCP_AGENT not in t.allowed_agents:
            continue
        server.add_tool(
            _make_handler(registry, t.name, t.input_schema), name=t.name, description=t.description
        )
    return server


def _make_handler(registry: ToolRegistry, name: str, schema: type) -> Any:
    async def handler(args: Any) -> dict[str, Any]:
        out = await registry.invoke(MCP_AGENT, name, args.model_dump())
        return out.model_dump()

    handler.__annotations__ = {"args": schema, "return": dict[str, Any]}
    return handler


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
