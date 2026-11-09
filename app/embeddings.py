"""Embedding client: turns text into vectors (RAG component 2).

Uses the OpenAI-compatible /v1/embeddings endpoint, which both providers offer:
  EMBEDDING_PROVIDER=ollama  free, local:  ollama pull nomic-embed-text      (768 dimensions)
  EMBEDDING_PROVIDER=openai  paid:         text-embedding-3-small            (1536 dimensions)
  EMBEDDING_PROVIDER=none    no vectors; retrieval falls back to keyword search

Changing the embedding model changes the vectors (and often their size), so every
chunk must be re-embedded with the new model: POST /knowledge/sync does that.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import List, Optional

DEFAULT_MODELS = {"ollama": "nomic-embed-text", "openai": "text-embedding-3-small"}


class Embedder:
    def __init__(self):
        self.provider = os.getenv("EMBEDDING_PROVIDER", "ollama").strip().lower()
        self.model = os.getenv("EMBEDDING_MODEL", "").strip() or DEFAULT_MODELS.get(self.provider, "")
        if self.provider == "openai":
            self.base_url = os.getenv("EMBEDDING_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
            self.api_key = os.getenv("OPENAI_API_KEY", "")
        else:
            self.base_url = os.getenv("EMBEDDING_BASE_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
            self.api_key = "ollama"
        self.base_url = self.base_url.rstrip("/")
        self.timeout = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "60"))
        self.batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
        self.last_error = ""
        self.last_ok: Optional[float] = None
        self._down_until = 0.0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        if self.provider not in DEFAULT_MODELS:
            return False
        return bool(self.api_key) if self.provider == "openai" else True

    def _prefix(self, kind: str) -> str:
        # nomic-embed-text is trained with task prefixes; other models ignore this
        if "nomic" in self.model.lower():
            return "search_query: " if kind == "query" else "search_document: "
        return ""

    def _post(self, inputs: List[str]) -> List[List[float]]:
        body = json.dumps({"model": self.model, "input": inputs}).encode("utf-8")
        req = urllib.request.Request(f"{self.base_url}/embeddings", data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        data = sorted(payload.get("data", []), key=lambda d: d.get("index", 0))
        vectors = [d["embedding"] for d in data]
        if len(vectors) != len(inputs):
            raise ValueError(f"expected {len(inputs)} embeddings, got {len(vectors)}")
        return vectors

    def embed(self, texts: List[str], kind: str = "document") -> Optional[List[List[float]]]:
        """Vectors for texts, or None if embeddings are off or the provider is unreachable."""
        if not texts or not self.enabled:
            return None
        if time.time() < self._down_until:  # back off for a minute after a failure
            return None
        prefix = self._prefix(kind)
        out: List[List[float]] = []
        try:
            with self._lock:
                for i in range(0, len(texts), self.batch_size):
                    out.extend(self._post([prefix + t for t in texts[i:i + self.batch_size]]))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            return self._fail(f"HTTP {exc.code} from {self.base_url}/embeddings: {detail}")
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            return self._fail(f"{type(exc).__name__}: {exc}")
        self.last_ok, self.last_error = time.time(), ""
        return out

    def embed_query(self, text: str) -> Optional[List[float]]:
        vectors = self.embed([text], kind="query")
        return vectors[0] if vectors else None

    def _fail(self, message: str):
        self.last_error = message
        self._down_until = time.time() + 60
        return None

    def reset_backoff(self) -> None:
        self._down_until = 0.0

    def status(self) -> dict:
        return {"provider": self.provider, "model": self.model, "base_url": self.base_url,
                "enabled": self.enabled, "last_error": self.last_error,
                "last_success": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.last_ok)) if self.last_ok else None}


embedder = Embedder()
