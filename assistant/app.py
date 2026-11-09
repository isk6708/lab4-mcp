"""Lab 4 AI Assistant: the MCP host.

  browser UI  ->  this app (MCP host + agent loop)  ->  MCP client  ->  Lab 4 MCP server  ->  product API
                          |
                          +-> LLM provider (OpenAI paid / Ollama free), chosen per request in the UI

Run locally:  uvicorn assistant.app:app --port 8300   (reads .env from the project root)
"""
import base64
import builtins
import os
from collections import OrderedDict
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)  # real environment variables (Kubernetes) win

import openai  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from assistant import mcp_client  # noqa: E402
from assistant.agent import Agent, Conversation  # noqa: E402
from assistant.llm import client_for, load_providers, provider_status  # noqa: E402

APPROVAL_TOOLS = [t.strip() for t in os.getenv(
    "APPROVAL_TOOLS",
    "create_product,update_product,delete_product,upload_product_attachment,delete_attachment").split(",") if t.strip()]
DEFAULT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "6"))
MAX_CONVERSATIONS = 200
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Lab 4 AI Assistant (MCP host)")
conversations: "OrderedDict[str, Conversation]" = OrderedDict()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def get_conversation(conversation_id: Optional[str]) -> Conversation:
    if conversation_id and conversation_id in conversations:
        conversations.move_to_end(conversation_id)
        return conversations[conversation_id]
    conv = Conversation()
    conversations[conv.id] = conv
    while len(conversations) > MAX_CONVERSATIONS:
        conversations.popitem(last=False)
    return conv


def root_cause(exc: BaseException) -> BaseException:
    """MCP clients run inside task groups, so errors arrive wrapped in ExceptionGroups."""
    group_type = getattr(builtins, "BaseExceptionGroup", None)  # Python 3.11+; absent on 3.10
    while group_type and isinstance(exc, group_type) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def friendly_error(exc: BaseException, provider_id: str, model: str) -> str:
    exc = root_cause(exc)
    if isinstance(exc, openai.AuthenticationError):
        return "OpenAI rejected the API key. Check OPENAI_API_KEY in .env."
    if isinstance(exc, openai.NotFoundError):
        hint = f" Run:  ollama pull {model}" if provider_id == "ollama" else ""
        return f"Model '{model}' was not found.{hint}"
    if isinstance(exc, openai.RateLimitError):
        return "The provider rate-limited the request or the account has no credit left."
    if isinstance(exc, openai.BadRequestError):
        return f"The model rejected the request ({exc}). Small models sometimes do not support tools; try qwen2.5:7b or switch mode."
    if isinstance(exc, openai.APIConnectionError):
        return f"Cannot reach the {provider_id} endpoint. Is it running? ({exc})"
    if isinstance(exc, openai.APITimeoutError):
        return "The model took too long to answer. Local models on CPU can be slow; try a smaller model."
    name = type(exc).__name__
    if "Connect" in name or "Timeout" in name or isinstance(exc, (ConnectionError, OSError)):
        return f"Cannot connect to the MCP server via {mcp_client.describe()}: {exc}"
    return f"{name}: {exc}"


async def run_turn(conv: Conversation, provider_id: str, model: str, mode: str,
                   max_steps: int, decision: Optional[str] = None) -> dict:
    providers = load_providers()
    provider = providers.get(provider_id)
    if not provider:
        raise HTTPException(400, f"Unknown provider '{provider_id}'")
    if provider.id == "openai" and not provider.api_key:
        raise HTTPException(400, "OpenAI is not configured: set OPENAI_API_KEY in .env")

    llm = client_for(provider)
    steps = {"chat": 1, "assistant": 1}.get(mode, max(1, min(max_steps, 15)))
    common = dict(max_steps=steps, approval_tools=APPROVAL_TOOLS, temperature=provider.temperature,
                  supports_tool_choice=(provider.id == "openai"))

    snap = conv.snapshot()
    try:
        if mode == "chat":
            result = await Agent(llm, model, None, [], use_tools=False, **common).run(conv, decision)
        else:
            async with mcp_client.mcp_session() as session:
                listed = await session.list_tools()

                async def call_tool(name: str, args: dict) -> str:
                    return mcp_client.result_text(await session.call_tool(name, args))

                agent = Agent(llm, model, call_tool, mcp_client.to_openai_tools(listed.tools), **common)
                result = await agent.run(conv, decision)
    except HTTPException:
        conv.restore(snap)
        raise
    except Exception as exc:  # noqa: BLE001 - report every failure to the UI
        conv.restore(snap)
        return {"status": "error", "answer": friendly_error(exc, provider.id, model), "trace": [],
                "conversation_id": conv.id}
    return {**result, "conversation_id": conv.id, "provider": provider.id, "model": model, "mode": mode}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class TurnSettings(BaseModel):
    provider: str
    model: str
    mode: Literal["agent", "assistant", "chat"] = "agent"
    max_steps: int = Field(DEFAULT_MAX_STEPS, ge=1, le=15)


class ChatRequest(TurnSettings):
    conversation_id: Optional[str] = None
    message: str = Field(..., min_length=1, max_length=8000)


class ApproveRequest(TurnSettings):
    conversation_id: str
    decision: Literal["approve", "reject"]


class ResetRequest(BaseModel):
    conversation_id: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "healthy", "component": "lab4-assistant"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
async def config():
    providers = load_providers()
    default_provider = os.getenv("LLM_DEFAULT_PROVIDER", "ollama")
    statuses = [await provider_status(p) for p in providers.values()]
    return {
        "providers": statuses,
        "default_provider": default_provider if default_provider in providers else next(iter(providers), None),
        "default_model": os.getenv("LLM_DEFAULT_MODEL", ""),
        "mcp_transport": mcp_client.describe(),
        "approval_tools": APPROVAL_TOOLS,
        "default_max_steps": DEFAULT_MAX_STEPS,
    }


@app.get("/api/tools")
async def tools():
    try:
        async with mcp_client.mcp_session() as session:
            listed = await session.list_tools()
            return {"transport": mcp_client.describe(),
                    "tools": [{"name": t.name, "description": (t.description or "").strip(),
                               "requires_approval": t.name in APPROVAL_TOOLS} for t in listed.tools]}
    except Exception as exc:  # noqa: BLE001
        return {"transport": mcp_client.describe(), "tools": [],
                "error": friendly_error(exc, "mcp", "")}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    conv = get_conversation(req.conversation_id)
    if conv.pending:  # user typed a new message instead of answering the approval card
        conv.abandon_pending("Not executed: the user sent a new message instead of approving this action.")
    conv.messages.append({"role": "user", "content": req.message})
    return await run_turn(conv, req.provider, req.model, req.mode, req.max_steps)


@app.post("/api/approve")
async def approve(req: ApproveRequest):
    conv = conversations.get(req.conversation_id)
    if not conv or not conv.pending:
        raise HTTPException(409, "Nothing is waiting for approval in this conversation")
    return await run_turn(conv, req.provider, req.model, req.mode, req.max_steps, decision=req.decision)


@app.post("/api/attach")
async def attach(product_id: int = Form(...), conversation_id: Optional[str] = Form(None),
                 file: UploadFile = File(...)):
    """Button-triggered workflow (no prompt needed): upload a spec PDF through the MCP tool."""
    data = await file.read()
    conv = get_conversation(conversation_id)
    try:
        async with mcp_client.mcp_session() as session:
            result = await session.call_tool("upload_product_attachment", {
                "product_id": product_id, "filename": file.filename or "specification.pdf",
                "pdf_base64": base64.b64encode(data).decode("ascii")})
            text = mcp_client.result_text(result)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "answer": friendly_error(exc, "mcp", ""), "conversation_id": conv.id}
    ok = not getattr(result, "isError", False)
    note = (f"[Upload button] I attached '{file.filename}' to product {product_id}. Result: {text}"
            if ok else f"[Upload button] Attaching '{file.filename}' to product {product_id} failed: {text}")
    conv.messages.append({"role": "user", "content": note})
    conv.messages.append({"role": "assistant", "content": "Noted." if ok else "Noted, the upload failed."})
    return {"status": "done" if ok else "error", "answer": text, "conversation_id": conv.id}


@app.post("/api/reset")
def reset(req: ResetRequest):
    if req.conversation_id:
        conversations.pop(req.conversation_id, None)
    return {"status": "reset"}
