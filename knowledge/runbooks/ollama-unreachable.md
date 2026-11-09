---
title: Assistant says Ollama is not reachable
type: runbook
owner: ai-platform-team
last_updated: 2026-11-09
tags: [ollama, llm, embeddings, assistant, network, host.docker.internal]
---

# Assistant says Ollama is not reachable

## Symptoms
The assistant UI shows "Ollama not reachable", or `/knowledge/status` reports the embedding provider as unavailable and retrieval falls back to keyword mode.

## Likely causes
1. Ollama is not running on the Windows PC.
2. From Docker or Kubernetes, Ollama listens only on 127.0.0.1, so containers cannot reach it through `host.docker.internal`.
3. The model has not been pulled (`nomic-embed-text` for embeddings, `qwen2.5:7b` or `llama3.2` for chat).

## Diagnose
- On the PC: `ollama list` and `curl http://localhost:11434/api/tags`.
- From a container: `kubectl exec deploy/lab4-assistant -- python -c "import urllib.request;print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').read()[:200])"`.

## Fix
- Start Ollama from the Start menu or tray.
- Set the Windows environment variable `OLLAMA_HOST=0.0.0.0`, restart Ollama, and allow port 11434 if Windows Firewall asks.
- Pull missing models: `ollama pull nomic-embed-text`.
- After embeddings work again, run `POST /knowledge/sync` to embed any chunks stored while Ollama was down.

## Tacit knowledge
The first request after Ollama starts can take 20 to 60 seconds while the model loads into memory. A single slow answer right after a restart is normal; only investigate if it stays slow.
