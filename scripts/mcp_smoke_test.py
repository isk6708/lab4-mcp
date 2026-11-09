"""Smoke-test the Lab 4 MCP server directly: no LLM, no assistant.

Read-only checks (safe to run any time):
    python scripts/mcp_smoke_test.py                                   # stdio: starts the server itself
    python scripts/mcp_smoke_test.py --transport http                  # connects to MCP_URL or http://localhost:8200/mcp
    python scripts/mcp_smoke_test.py --transport http --url http://mcp.platform.local/mcp

Full round trip (creates a temporary product, uploads the sample PDF, searches it, then deletes it):
    python scripts/mcp_smoke_test.py --write

The MCP server finds the product API through API_BASE_URL (from .env or the environment).
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

from assistant.mcp_client import result_text  # noqa: E402

SAMPLE_PDF = ROOT / "samples" / "platform-component-spec.pdf"
failures = []


async def call(session: ClientSession, name: str, args: dict = None, expect_error: bool = False):
    args = args or {}
    result = await session.call_tool(name, args)
    text = result_text(result, 100_000)  # full text so ids can be parsed
    ok = bool(result.isError) == expect_error
    if not ok:
        failures.append(name)
    shown = text if len(text) <= 400 else text[:400] + " ..."
    print(f"\n[{'PASS' if ok else 'FAIL'}] {name} {json.dumps(args)}\n    {shown}")
    return text


async def run_checks(session: ClientSession, write: bool) -> None:
    tools = (await session.list_tools()).tools
    print(f"Connected. {len(tools)} tools: {', '.join(t.name for t in tools)}")
    if len(tools) != 12:
        failures.append("tool count")

    await call(session, "health_check")
    await call(session, "list_products")
    await call(session, "search_product_specs", {"query": "warranty"})
    await call(session, "get_product", {"product_id": 999999}, expect_error=True)  # error path

    if not write:
        print("\nRead-only checks done. Add --write for the full create/upload/search/delete round trip.")
        return

    created = json.loads(await call(session, "create_product", {
        "name": "MCP smoke test", "description": "temporary", "price": 1.0, "stock": 1}))
    pid = created["id"]
    try:
        await call(session, "update_product", {"product_id": pid, "stock": 2})
        uploaded = json.loads(await call(session, "upload_product_attachment",
                                         {"product_id": pid, "file_path": str(SAMPLE_PDF)}))
        aid = uploaded["id"]
        await call(session, "list_product_attachments", {"product_id": pid})
        await call(session, "get_attachment", {"attachment_id": aid})
        await call(session, "get_attachment_chunks", {"attachment_id": aid, "page_number": 2})
        await call(session, "search_product_specs", {"query": "warranty", "product_id": pid})
        await call(session, "delete_attachment", {"attachment_id": aid})
    finally:
        await call(session, "delete_product", {"product_id": pid})


async def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the Lab 4 MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--url", default=os.getenv("MCP_URL", "http://localhost:8200/mcp"))
    parser.add_argument("--write", action="store_true", help="also create, upload, search and delete")
    args = parser.parse_args()

    print(f"API_BASE_URL = {os.getenv('API_BASE_URL', 'http://platform.local')}")
    if args.transport == "stdio":
        print("Transport    = stdio (starting python -m mcp_server.server)")
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "mcp_server.server", "--transport", "stdio"],
                                       env=dict(os.environ), cwd=str(ROOT))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await run_checks(session, args.write)
    else:
        print(f"Transport    = streamable HTTP ({args.url})")
        async with streamablehttp_client(args.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await run_checks(session, args.write)

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
