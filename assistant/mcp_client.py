"""MCP client side of the host: connects to the Lab 4 MCP server over stdio or streamable HTTP.

MCP_TRANSPORT=stdio            -> starts `python -m mcp_server.server --transport stdio` as a child process
MCP_TRANSPORT=streamable_http  -> connects to MCP_URL (e.g. http://mcp.platform.local/mcp)
"""
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, List

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def transport() -> str:
    return os.getenv("MCP_TRANSPORT", "stdio").lower().replace("-", "_")


def describe() -> str:
    if transport() == "stdio":
        return "stdio (child process: python -m mcp_server.server)"
    return f"streamable HTTP ({os.getenv('MCP_URL', 'http://localhost:8200/mcp')})"


@asynccontextmanager
async def mcp_session() -> AsyncIterator[ClientSession]:
    if transport() == "stdio":
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server", "--transport", "stdio"],
            env=dict(os.environ),  # pass API_BASE_URL etc. to the child
            cwd=str(PROJECT_ROOT),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        url = os.getenv("MCP_URL", "http://localhost:8200/mcp")
        async with streamablehttp_client(url) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def to_openai_tools(mcp_tools) -> List[dict]:
    """MCP tool list -> the `tools` parameter of the Chat Completions API."""
    return [{
        "type": "function",
        "function": {
            "name": t.name,
            "description": (t.description or "").strip(),
            "parameters": t.inputSchema or {"type": "object", "properties": {}},
        },
    } for t in mcp_tools]


def result_text(result, limit: int = 6000) -> str:
    """Flatten an MCP CallToolResult into text for the LLM (trimmed to protect small context windows)."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        # FastMCP wraps non-dict returns as {"result": ...}
        value = structured.get("result", structured) if isinstance(structured, dict) else structured
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        parts = [getattr(c, "text", "") for c in (result.content or [])]
        text = "\n".join(p for p in parts if p)
    if getattr(result, "isError", False):
        text = f"ERROR: {text}"
    if len(text) > limit:
        text = text[:limit] + f"... [truncated {len(text) - limit} chars]"
    return text or "(no output)"
