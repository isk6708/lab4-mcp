"""LLM providers, configured from .env and switchable per request from the UI.

Both providers speak the OpenAI Chat Completions API with tool calling:
  - openai : paid, needs OPENAI_API_KEY (an API key from platform.openai.com with billing;
             a ChatGPT Plus/Pro subscription does NOT include API access)
  - ollama : free, runs models such as llama3.2 or qwen2.5 on your own machine
             through Ollama's OpenAI-compatible endpoint (/v1)
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx
from openai import AsyncOpenAI


def _csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Provider:
    id: str
    label: str
    base_url: str
    api_key: str
    models: List[str]
    paid: bool
    temperature: Optional[float] = None  # None = provider default (some OpenAI reasoning models reject it)
    extra: Dict = field(default_factory=dict)


def load_providers() -> Dict[str, Provider]:
    providers = {
        "ollama": Provider(
            id="ollama",
            label="Ollama (free, local)",
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
            api_key="ollama",  # required by the client library, ignored by Ollama
            models=_csv(os.getenv("OLLAMA_MODELS", "llama3.2,qwen2.5:7b")),
            paid=False,
            temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0.1")),
        ),
        "openai": Provider(
            id="openai",
            label="OpenAI (paid API)",
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            models=_csv(os.getenv("OPENAI_MODELS", "gpt-4.1-mini,gpt-5-mini")),
            paid=True,
        ),
    }
    enabled = _csv(os.getenv("LLM_PROVIDERS", "ollama,openai"))
    return {k: v for k, v in providers.items() if k in enabled}


def _same_model(configured: str, installed: str) -> bool:
    return installed == configured or installed == f"{configured}:latest"


async def provider_status(p: Provider) -> dict:
    """What the UI shows in the provider/model dropdowns."""
    info = {"id": p.id, "label": p.label, "paid": p.paid, "available": False,
            "models": [{"name": m, "ready": False} for m in p.models], "message": ""}
    if p.id == "openai":
        if p.api_key:
            info["available"] = True
            for m in info["models"]:
                m["ready"] = True
        else:
            info["message"] = "Set OPENAI_API_KEY in .env to enable"
        return info

    # Ollama: ask which models are pulled
    root = p.base_url[:-3] if p.base_url.endswith("/v1") else p.base_url
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{root}/api/tags")
            resp.raise_for_status()
            installed = [m["name"] for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        info["message"] = f"Ollama not reachable at {root}"
        return info
    info["available"] = True
    for m in info["models"]:
        m["ready"] = any(_same_model(m["name"], i) for i in installed)
    # also offer pulled models that are not listed in .env
    for name in installed:
        if not any(_same_model(m["name"], name) for m in info["models"]):
            info["models"].append({"name": name, "ready": True})
    if not any(m["ready"] for m in info["models"]):
        info["message"] = "No models pulled yet: run  ollama pull llama3.2"
    return info


def client_for(p: Provider) -> AsyncOpenAI:
    return AsyncOpenAI(base_url=p.base_url, api_key=p.api_key or "missing",
                       timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "300")))
