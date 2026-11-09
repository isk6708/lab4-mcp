"""Lab 4 MCP server: exposes the Product & Spec API as MCP tools.

It is a thin adapter over the FastAPI REST API (same pattern as the book's
"wrap a list-deployments API with MCP" example): the API stays the source of
truth, and MCP gives any AI host a standard way to call it.

Run it either way:
    python -m mcp_server.server --transport stdio              # for Claude Desktop, the Lab 4 assistant (stdio mode)
    python -m mcp_server.server --transport streamable-http    # http://localhost:8200/mcp

Environment:
    API_BASE_URL     where the FastAPI app is   (default http://platform.local)
    PUBLIC_API_URL   URL users open in a browser for downloads (default = API_BASE_URL)
    MCP_HOST / MCP_PORT                         (default 127.0.0.1 / 8200)
    MCP_ALLOWED_HOSTS  comma list for DNS-rebinding protection on HTTP transport
"""
import argparse
import base64
import os
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

try:  # read the project's .env when run on its own (real environment variables still win)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

API_BASE_URL = os.getenv("API_BASE_URL", "http://platform.local").rstrip("/")
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", API_BASE_URL).rstrip("/")
HTTP_TIMEOUT = float(os.getenv("API_TIMEOUT_SECONDS", "60"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
DEFAULT_ALLOWED_HOSTS = ("localhost,localhost:*,127.0.0.1,127.0.0.1:*,"
                         "mcp.platform.local,mcp.platform.local:*,"
                         "lab4-mcp-service,lab4-mcp-service:*")


def _csv(name: str, default: str) -> list:
    return [h.strip() for h in os.getenv(name, default).split(",") if h.strip()]


def build_server(host: str, port: int) -> FastMCP:
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_csv("MCP_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS),
        allowed_origins=_csv("MCP_ALLOWED_ORIGINS",
                             "http://localhost:*,http://127.0.0.1:*,http://assistant.platform.local"),
    )
    return FastMCP(
        "lab4-product-specs",
        instructions=(
            "Tools for the Lab 4 product catalogue. Products can have PDF specification "
            "sheets; their text is split into page-tagged chunks. Use search_product_specs "
            "to find facts in specs and cite the filename and page number. Never guess ids: "
            "list or search first."
        ),
        host=host,
        port=port,
        stateless_http=True,   # no sticky sessions needed behind a Kubernetes Service
        json_response=True,
        transport_security=security,
    )


# The decorators below need a server object at import time; host/port are
# re-applied in main() before run().
mcp = build_server(os.getenv("MCP_HOST", "127.0.0.1"), int(os.getenv("MCP_PORT", "8200")))


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
async def _request(method: str, path: str, **kwargs) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=HTTP_TIMEOUT) as client:
        try:
            resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Cannot reach the product API at {API_BASE_URL}: {exc}") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise RuntimeError(f"API returned {resp.status_code}: {detail}")
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


# ---------------------------------------------------------------------------
# Tools: health and products
# ---------------------------------------------------------------------------
@mcp.tool()
async def health_check() -> dict:
    """Check whether the product API is up."""
    return await _request("GET", "/health")


@mcp.tool()
async def list_products(skip: int = 0, limit: int = 100) -> list:
    """List products in the catalogue (id, name, description, price, stock)."""
    return await _request("GET", "/products", params={"skip": skip, "limit": limit})


@mcp.tool()
async def get_product(product_id: int) -> dict:
    """Get one product, including its attached PDF specification sheets."""
    return await _request("GET", f"/products/{product_id}")


@mcp.tool()
async def create_product(name: str, price: float, stock: int, description: Optional[str] = None) -> dict:
    """Create a new product. Changes data: requires user approval in the Lab 4 assistant."""
    body = {"name": name, "description": description, "price": price, "stock": stock}
    return await _request("POST", "/products", json=body)


@mcp.tool()
async def update_product(product_id: int, name: Optional[str] = None, description: Optional[str] = None,
                         price: Optional[float] = None, stock: Optional[int] = None) -> dict:
    """Update some fields of a product; fields left empty keep their current value.
    Changes data: requires user approval in the Lab 4 assistant."""
    current = await _request("GET", f"/products/{product_id}")
    body = {
        "name": name if name is not None else current["name"],
        "description": description if description is not None else current.get("description"),
        "price": price if price is not None else current["price"],
        "stock": stock if stock is not None else current["stock"],
    }
    return await _request("PUT", f"/products/{product_id}", json=body)


@mcp.tool()
async def delete_product(product_id: int) -> dict:
    """Delete a product and all its spec attachments. Irreversible: requires user approval."""
    await _request("DELETE", f"/products/{product_id}")
    return {"deleted_product_id": product_id}


# ---------------------------------------------------------------------------
# Tools: PDF specification attachments
# ---------------------------------------------------------------------------
@mcp.tool()
async def list_product_attachments(product_id: int) -> list:
    """List the PDF specification sheets attached to a product."""
    return await _request("GET", f"/products/{product_id}/attachments")


@mcp.tool()
async def get_attachment(attachment_id: int) -> dict:
    """Get metadata and a text preview of one attachment, plus a browser download link."""
    data = await _request("GET", f"/attachments/{attachment_id}")
    data["download_url"] = f"{PUBLIC_API_URL}/attachments/{attachment_id}/download"
    return data


@mcp.tool()
async def get_attachment_chunks(attachment_id: int, page_number: Optional[int] = None, max_chunks: int = 20) -> list:
    """Read the text of a spec sheet as page-tagged chunks (optionally one page only)."""
    chunks = await _request("GET", f"/attachments/{attachment_id}/chunks")
    if page_number is not None:
        chunks = [c for c in chunks if c["page_number"] == page_number]
    return chunks[:max_chunks]


@mcp.tool()
async def search_product_specs(query: str, product_id: Optional[int] = None, limit: int = 5) -> list:
    """Keyword search across all PDF spec sheets (or one product's). Returns snippets with
    product, filename and page number. Use short keywords, e.g. 'warranty' or 'memory'."""
    params = {"q": query, "limit": limit}
    if product_id is not None:
        params["product_id"] = product_id
    return await _request("GET", "/search", params=params)


@mcp.tool()
async def search_knowledge(question: str, top_k: int = 5, product_id: Optional[int] = None,
                           source: str = "all", doc_type: Optional[str] = None) -> dict:
    """Semantic search (by meaning) over product spec sheets AND the platform knowledge base
    (runbooks, postmortems, blueprints). Use it for 'why', 'how do I', troubleshooting and
    standards questions, or when exact keywords may not appear. Returns ranked chunks with a
    citation, relevance_score and last_updated; cite them in your answer.
    source: all | specs | knowledge. doc_type: runbook | postmortem | blueprint."""
    params = {"q": question, "top_k": top_k, "source": source}
    if product_id is not None:
        params["product_id"] = product_id
    if doc_type:
        params["doc_type"] = doc_type
    return await _request("GET", "/retrieve", params=params)


@mcp.tool()
async def list_knowledge_documents() -> list:
    """List the knowledge base documents (runbooks, postmortems, blueprints) with owner and last-updated date."""
    return await _request("GET", "/knowledge")


@mcp.tool()
async def upload_product_attachment(product_id: int, file_path: Optional[str] = None,
                                    pdf_base64: Optional[str] = None,
                                    filename: Optional[str] = None) -> dict:
    """Attach a PDF spec sheet to a product. Give either file_path (a PDF on the machine
    running this MCP server, e.g. with stdio) or pdf_base64. Requires user approval."""
    if file_path:
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise RuntimeError(f"File not found on the MCP server machine: {path}")
        data = path.read_bytes()
        filename = filename or path.name
    elif pdf_base64:
        try:
            data = base64.b64decode(pdf_base64, validate=True)
        except ValueError as exc:
            raise RuntimeError("pdf_base64 is not valid base64") from exc
        filename = filename or "specification.pdf"
    else:
        raise RuntimeError("Provide file_path or pdf_base64")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise RuntimeError(f"File larger than {MAX_UPLOAD_MB} MB")
    files = {"file": (filename, data, "application/pdf")}
    return await _request("POST", f"/products/{product_id}/attachments", files=files)


@mcp.tool()
async def delete_attachment(attachment_id: int) -> dict:
    """Delete a spec attachment, its chunks and its file. Irreversible: requires user approval."""
    await _request("DELETE", f"/attachments/{attachment_id}")
    return {"deleted_attachment_id": attachment_id}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Lab 4 MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"],
                        default=os.getenv("MCP_SERVER_TRANSPORT", "stdio"))
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8200")))
    args = parser.parse_args()

    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # Never print to stdout in stdio mode: stdout carries the MCP protocol.
        print(f"Lab 4 MCP server on http://{args.host}:{args.port}{mcp.settings.streamable_http_path} "
              f"-> API {API_BASE_URL}", flush=True)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
