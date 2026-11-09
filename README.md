# Lab 4 – Platform Engineering with MCP and Agentic AI

A product catalogue API whose products carry **PDF specification sheets**, exposed to AI through an **MCP server**, and used through an **AI Assistant (MCP host)** that runs an **agent loop** with either **paid OpenAI** or **free local Ollama** models.

You can run it three ways:

- **Local, no Docker:** everything runs as Python processes on your PC. Fastest for development and testing (section 5).
- **Docker, no Kubernetes:** three containers with Docker Compose, one command to start (section 6).
- **Kubernetes:** Docker Desktop Kubernetes with an Ingress, like a real platform (sections 7–12).

Companion to *Agentic AI for Platform Engineering* (Packt, 2026); chapter summaries are in `notes\`.

---

## Contents

1. [Architecture](#1-architecture)
2. [Project structure](#2-project-structure)
3. [Prerequisites](#3-prerequisites)
4. [Common setup (all ways)](#4-common-setup-all-ways)
5. [Run and test locally without Docker](#5-run-and-test-locally-without-docker)
6. [Run with Docker (no Kubernetes)](#6-run-with-docker-no-kubernetes)
7. [Kubernetes: one-time setup](#7-kubernetes-one-time-setup)
8. [Kubernetes: deploy the product API](#8-kubernetes-deploy-the-product-api)
9. [Using the product and PDF spec API](#9-using-the-product-and-pdf-spec-api)
10. [Knowledge base and RAG (Chapter 4)](#10-knowledge-base-and-rag-chapter-4)
11. [Angular frontends: without and with login](#11-angular-frontends-without-and-with-login)
12. [Kubernetes: deploy the AI layer](#12-kubernetes-deploy-the-ai-layer)
13. [.env reference](#13-env-reference)
14. [The MCP server](#14-the-mcp-server)
15. [Use the MCP server from Claude Desktop and other hosts](#15-use-the-mcp-server-from-claude-desktop-and-other-hosts)
16. [What is agentic AI? (with this lab)](#16-what-is-agentic-ai-with-this-lab)
17. [Demo script for class](#17-demo-script-for-class)
18. [Updating after code changes](#18-updating-after-code-changes)
19. [Troubleshooting](#19-troubleshooting)
20. [Clean up](#20-clean-up)
21. [Limitations and security notes](#21-limitations-and-security-notes)

---

## 1. Architecture

```
 Browser ─► AI Assistant (MCP host) ───────────────────► LLM: OpenAI (paid) or Ollama (free, on your PC)
              │ agent loop + approvals
              │ MCP client (stdio or streamable HTTP)
              ▼
 Claude Desktop / VS Code / Claude Code ─► MCP server (14 tools)
                                               │ HTTP
                                               ▼
 curl / Swagger ──────────────────────────► Product API (FastAPI)
                                               │                       ╲
                                   data\app_platform.db             Ollama nomic-embed-text
                                   data\uploads\*.pdf               (embeddings for RAG)
                                   knowledge\*.md (runbooks, postmortem, blueprint)
```

| Component | Local (no Docker) | Docker Compose | Kubernetes |
|---|---|---|---|
| Product API | http://localhost:8121 | http://localhost:8121 | http://platform.local |
| MCP server (streamable HTTP) | http://localhost:8200/mcp | http://localhost:8200/mcp | http://mcp.platform.local/mcp |
| MCP server (stdio) | started by the host as a child process | inside the assistant container (optional) | inside the assistant pod (optional) |
| AI Assistant | http://localhost:8300 | http://localhost:8300 | http://assistant.platform.local |
| Data | `.\data\` in the project folder | Docker volume `lab4_lab4-data` | PVC `lab4-data` |
| Start with | section 5 | section 6 | sections 7–12 |

Kubernetes objects:

| Component | Image | Objects |
|---|---|---|
| Product API | `lab4-fastapi:v2` | `platform-eng-app`, `platform-eng-service`, `platform-eng-ingress`, PVC `lab4-data`, Secret `platform-local-secrets` |
| MCP server | `lab4-ai:v1` | `lab4-mcp-server`, `lab4-mcp-service` |
| AI Assistant | `lab4-ai:v1` | `lab4-assistant`, `lab4-assistant-service`, Secret `lab4-ai-env` |
| Ingress for AI | | `lab4-ai-ingress` |

---

## 2. Project structure

```
lab4-platform-engineering\
├── app\                        Product API (FastAPI)
│   ├── main.py                   products, PDF attachments, chunks, keyword search
│   ├── db.py                     database models (products, specs, chunks, knowledge documents)
│   ├── auth.py / tokens.py       login (POST /token), JWT creation and checking, get_current_user
│   ├── pdf_utils.py              PDF check, text extraction, page-aware chunking
│   ├── knowledge.py              Markdown metadata header + heading-aware chunking
│   ├── embeddings.py             embedding client (Ollama or OpenAI)
│   ├── vectors.py                cosine similarity, keyword scoring, ranking
│   └── knowledge_service.py      knowledge sync, embedding, retrieval (/retrieve)
├── mcp_server\
│   └── server.py               MCP server: 14 tools wrapping the API (stdio / streamable HTTP)
├── assistant\                  AI Assistant = MCP host
│   ├── app.py                    web API + approval handling + upload button
│   ├── agent.py                  the agent loop (ReAct), step budget, approvals
│   ├── rag.py                    RAG mode and /api/ask: retrieve, then answer with citations
│   ├── llm.py                    OpenAI / Ollama providers from .env
│   ├── mcp_client.py             MCP client over stdio or streamable HTTP
│   └── static\index.html         chat UI
├── frontend\
│   ├── angular-products-basic\  Angular v1: product CRUD, no login (port 4200)
│   └── angular-products-auth\   Angular v2: JWT login, guard, interceptor, then CRUD (port 4201)
├── knowledge\                  organizational knowledge for RAG (runbooks, postmortem, blueprint)
├── scripts\
│   ├── mcp_smoke_test.py       tests the MCP server directly, no LLM needed
│   └── rag_eval.py             measures retrieval hit rate and MRR
├── samples\
│   └── platform-component-spec.pdf   demo spec sheet (fictional, 2 pages)
├── notes\                      chapter summaries (.docx)
├── data\                       created on first local run (SQLite DB + uploaded PDFs)
├── Dockerfile                  image for the product API
├── Dockerfile.ai               image for the MCP server and the assistant
├── requirements.txt            product API dependencies
├── requirements-ai.txt         MCP server + assistant dependencies
├── docker-compose.yml          all three services on Docker (no Kubernetes)
├── k8s-local.yaml              product API on Kubernetes
├── k8s-ai.yaml                 MCP server + assistant on Kubernetes
├── .env.example                settings template (copy to .env)
├── claude_desktop_config.example.json   Claude Desktop (stdio) example
├── .gitignore / .dockerignore
└── README.md                   this file
```

---

## 3. Prerequisites

| Tool | Needed for | Check |
|---|---|---|
| **Python 3.11+** (3.10 works) | local runs, MCP server, Claude Desktop | `python --version` |
| **Ollama** for Windows (ollama.com) | free local models | `ollama --version` |
| **OpenAI API key** (optional) | paid models | from https://platform.openai.com/api-keys, billing enabled |
| **curl** | testing (built into Windows 10/11) | `curl --version` |
| **Docker Desktop** | Docker and Kubernetes paths | `docker compose version` |
| Kubernetes enabled in Docker Desktop (Settings → Kubernetes) | Kubernetes path only | `kubectl get nodes` shows `docker-desktop  Ready` |
| Node.js (optional) | MCP Inspector | `node --version` |

> **A ChatGPT Plus/Pro subscription does not include API access.** The assistant needs an API key from platform.openai.com with credit on the account. Without one, use Ollama for free.

Commands are for **cmd**. In **PowerShell**:
- type `curl.exe` instead of `curl`
- activate the venv with `.venv\Scripts\Activate.ps1` (if blocked: `Set-ExecutionPolicy -Scope Process Bypass`)
- set a variable with `$env:NAME="value"` instead of `set NAME=value`

---

## 4. Common setup (all ways)

### 4.1 Python virtual environment

```cmd
cd C:\RajWork\2026\ESLearning\mcp\lab4-platform-engineering
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-ai.txt
```

Every new terminal needs `cd` into the folder and `.venv\Scripts\activate` again.

### 4.2 Settings file

```cmd
copy .env.example .env
notepad .env
```

The defaults in `.env.example` are set for the **local** path (`localhost` URLs). For Docker and Kubernetes, `docker-compose.yml` and `k8s-ai.yaml` set the container URLs themselves, so the same `.env` works for all three. Add `OPENAI_API_KEY` if you have one. All settings are explained in section 13.

`.env` holds secrets. It is in `.gitignore` and `.dockerignore`, so it is never committed or baked into an image.

### 4.3 Free models

```cmd
ollama pull qwen2.5:7b
ollama pull llama3.2
ollama pull nomic-embed-text
ollama list
```

| Model | Size | Notes |
|---|---|---|
| `nomic-embed-text` | ~270 MB | Embedding model for RAG (turns text into vectors); not a chat model |
| `qwen2.5:7b` | ~4.7 GB | Recommended free default; follows tool instructions well |
| `llama3.2` | ~2 GB (3B) | Fast but small; sometimes picks the wrong tool. Good for showing why model size matters for agents |

Any other model you pull that supports tool calling appears in the assistant's model list automatically.

---

## 5. Run and test locally without Docker

No Docker, no Kubernetes, no hosts file. You need four terminals at most; each one starts with:

```cmd
cd C:\RajWork\2026\ESLearning\mcp\lab4-platform-engineering
.venv\Scripts\activate
```

Ports used: **8121** (API), **8200** (MCP over HTTP), **8300** (assistant), **11434** (Ollama).

### 5.1 Terminal 1: start the product API

```cmd
uvicorn app.main:app --port 8121 --reload
```

`--reload` restarts it when you edit code in `app\`. Data goes to `.\data\app_platform.db` and `.\data\uploads\`. The console prints the seeded `admin` user on first start, then a `Knowledge sync:` line once `knowledge\` is loaded and embedded (needs Ollama with `nomic-embed-text`; without it, retrieval uses keyword mode).

Open Swagger UI at http://localhost:8121/docs. You can test every endpoint there, including the PDF upload button.

### 5.2 Terminal 2: test the API with curl

```cmd
set BASE=http://localhost:8121

curl %BASE%/health

curl -X POST "%BASE%/products" -H "Content-Type: application/json" -d "{\"name\":\"Platform Component\",\"description\":\"Lab4 Container Artifact\",\"price\":299.00,\"stock\":5}"

curl %BASE%/products

curl -X POST "%BASE%/products/1/attachments" -F "file=@samples\platform-component-spec.pdf;type=application/pdf"

curl %BASE%/attachments/1/chunks

curl "%BASE%/search?q=warranty"
```

All endpoints are listed in section 9.

### 5.3 Test the MCP server over stdio (no LLM)

The API from 5.1 must be running. The smoke-test script starts the MCP server as a child process, exactly as an MCP host would, and calls the tools directly:

```cmd
python scripts\mcp_smoke_test.py
```

It lists the 14 tools and runs read-only checks (`health_check`, `list_products`, `search_product_specs`, `list_knowledge_documents`, `search_knowledge`, and an expected "not found" error). It also prints whether retrieval runs in `vector` or `keyword` mode. For the full round trip (create a temporary product, upload the sample PDF, read page 2, search, then delete everything again):

```cmd
python scripts\mcp_smoke_test.py --write
```

It ends with `ALL CHECKS PASSED`, or names the failed tools.

> Running `python -m mcp_server.server --transport stdio` on its own just waits silently: stdio servers expect an MCP host on the other end of stdin/stdout. Press Ctrl+C to stop it. Use the smoke test or the Inspector instead.

### 5.4 Terminal 3: run the MCP server over streamable HTTP

```cmd
python -m mcp_server.server --transport streamable-http --port 8200
```

It prints `Lab 4 MCP server on http://127.0.0.1:8200/mcp -> API http://localhost:8121`. Test it from terminal 2:

```cmd
python scripts\mcp_smoke_test.py --transport http
python scripts\mcp_smoke_test.py --transport http --write
```

Opening http://localhost:8200/mcp in a browser shows an error. That is normal: the endpoint speaks MCP, not HTML.

### 5.5 Inspect the MCP server in a browser (optional, needs Node.js)

The MCP Inspector lets you click through every tool, fill in arguments and see raw results.

stdio:
```cmd
npx @modelcontextprotocol/inspector .venv\Scripts\python.exe -m mcp_server.server --transport stdio
```

Streamable HTTP (server from 5.4 running):
```cmd
npx @modelcontextprotocol/inspector
```
In the page that opens, choose transport **Streamable HTTP**, URL `http://localhost:8200/mcp`, then **Connect** → **Tools** → **List Tools**.

### 5.6 Terminal 4: run the AI Assistant

Make sure Ollama is running (tray icon) or `OPENAI_API_KEY` is set in `.env`.

```cmd
uvicorn assistant.app:app --port 8300 --reload
```

Open http://localhost:8300.

- With `MCP_TRANSPORT=stdio` (the default), the header shows `MCP: stdio (child process…)`. The assistant starts the MCP server itself, so terminal 3 is not needed.
- To use the HTTP server from 5.4 instead, set `MCP_TRANSPORT=streamable_http` in `.env` (`MCP_URL` is already `http://localhost:8200/mcp`) and restart the assistant. The header then shows `MCP: streamable HTTP (…)`.

The left panel should list 14 tools. Try the demo in section 17, and the RAG examples in section 10.

### 5.7 Use Claude Desktop with the local MCP server

See section 15. `claude_desktop_config.example.json` already points to `http://localhost:8121`.

### 5.8 Mixed: local AI layer against the Kubernetes API

To run the MCP server and assistant locally while the API runs in Kubernetes, set in `.env`:

```
API_BASE_URL=http://platform.local
PUBLIC_API_URL=http://platform.local
```

### 5.9 Reset local data

Stop the API (Ctrl+C), delete the `data` folder, and start it again. A fresh database and the `admin` user are created.

---

## 6. Run with Docker (no Kubernetes)

All three parts run as containers with **Docker Compose**, defined in `docker-compose.yml`. No Kubernetes, no Ingress, no hosts file. The URLs are the same as the local path, so every test in section 5 works unchanged.

| Service | Image | URL on your PC | Inside the compose network |
|---|---|---|---|
| `api` | `lab4-fastapi:v2` | http://localhost:8121 | `http://api:8121` |
| `mcp` | `lab4-ai:v1` | http://localhost:8200/mcp | `http://mcp:8200/mcp` |
| `assistant` | `lab4-ai:v1` | http://localhost:8300 | |
| Data | Docker volume `lab4_lab4-data` mounted at `/app/data` | | |

Ports are published on `127.0.0.1` only, so other machines cannot reach them.

Stop any local `uvicorn` / MCP processes from section 5 first; they use the same ports.

### 6.1 Start everything

Docker Desktop must be running (Kubernetes does not need to be enabled). `.env` from section 4.2 is used if present.

```cmd
cd C:\RajWork\2026\ESLearning\mcp\lab4-platform-engineering
docker compose up -d --build
docker compose ps
```

All three services should be `running`, and `api` should be `healthy`. The MCP server waits until the API is healthy before it starts.

### 6.2 Test it

```cmd
set BASE=http://localhost:8121
curl %BASE%/health
curl -X POST "%BASE%/products" -H "Content-Type: application/json" -d "{\"name\":\"Platform Component\",\"description\":\"Lab4 Container Artifact\",\"price\":299.00,\"stock\":5}"
curl -X POST "%BASE%/products/1/attachments" -F "file=@samples\platform-component-spec.pdf;type=application/pdf"
curl "%BASE%/search?q=warranty"
```

Test the containerised MCP server from your PC (venv from section 4.1 active):

```cmd
python scripts\mcp_smoke_test.py --transport http
python scripts\mcp_smoke_test.py --transport http --write
```

Open the assistant at http://localhost:8300. The header shows `MCP: streamable HTTP (http://mcp:8200/mcp)`.

### 6.3 How the containers are wired

- `api` reaches Ollama on your PC at `http://host.docker.internal:11434/v1` for embeddings, and reads `knowledge\` through a read-only mount, so edited runbooks only need `POST /knowledge/sync`.
- `mcp` reaches the API at `http://api:8121` (service name), and gives users download links on `http://localhost:8121`.
- `assistant` reaches the MCP server at `http://mcp:8200/mcp`, and Ollama on your PC at `http://host.docker.internal:11434/v1`.
- `docker-compose.yml` overrides the `.env` values that must differ inside containers (`API_BASE_URL`, `PUBLIC_API_URL`, `MCP_URL`, `OLLAMA_BASE_URL`, `MCP_ALLOWED_HOSTS`), so the same `.env` works for local, Docker and Kubernetes.
- To have the assistant start the MCP server inside its own container over **stdio** instead, set `MCP_TRANSPORT: stdio` under `assistant` in `docker-compose.yml` and run `docker compose up -d`.

### 6.4 Everyday commands

```cmd
:: logs (Ctrl+C to stop following)
docker compose logs -f
docker compose logs -f assistant

:: restart one service, e.g. after editing .env
docker compose up -d --force-recreate assistant mcp

:: rebuild after code changes
docker compose up -d --build

:: stop (data is kept in the volume)
docker compose down

:: stop and delete all data
docker compose down -v
```

### 6.5 Data survives restarts

```cmd
docker compose restart api
curl http://localhost:8121/products/1
```

The product and its attachment are still there because `/app/data` is on the `lab4-data` volume. Only `docker compose down -v` deletes it.

### 6.6 Without Compose (plain docker run, API only)

To run just the product API container by hand:

```cmd
docker build -t lab4-fastapi:v2 .
docker volume create lab4-data-manual
docker run -d --name lab4-api -p 127.0.0.1:8121:8121 -v lab4-data-manual:/app/data -e DATABASE_URL=sqlite:////app/data/app_platform.db lab4-fastapi:v2
curl http://localhost:8121/health
```

Stop and remove it with `docker rm -f lab4-api`. Compose is easier for all three services because it creates the network, names and start order for you.

---

## 7. Kubernetes: one-time setup

### 7.1 Hosts file

Open Notepad **as Administrator**, open `C:\Windows\System32\drivers\etc\hosts`, add these lines and save:

```
127.0.0.1   platform.local
127.0.0.1   assistant.platform.local
127.0.0.1   mcp.platform.local
```

```cmd
ipconfig /flushdns
ping platform.local
```

`ping` should resolve to 127.0.0.1.

### 7.2 Free port 80

The ingress controller listens on `localhost:80`. Stop anything else using it (Laragon's Apache/Nginx, IIS, XAMPP):

```cmd
netstat -ano | findstr :80
```

### 7.3 Install the ingress-nginx controller

```cmd
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/cloud/deploy.yaml
kubectl wait -n ingress-nginx --for=condition=ready pod -l app.kubernetes.io/component=controller --timeout=120s
kubectl get pods -n ingress-nginx
kubectl get svc -n ingress-nginx
```

`ingress-nginx-controller-…` should be `1/1 Running` (the admission jobs show `Completed`, which is normal), and the `ingress-nginx-controller` service should show `EXTERNAL-IP localhost`.

---

## 8. Kubernetes: deploy the product API

```cmd
cd C:\RajWork\2026\ESLearning\mcp\lab4-platform-engineering
docker build -t lab4-fastapi:v2 .
kubectl apply -f k8s-local.yaml
kubectl get pvc lab4-data
kubectl get pods
kubectl get ingress
```

`lab4-data` should be `Bound`, `platform-eng-app-…` should be `1/1 Running`, and the ingress host should be `platform.local`.

```cmd
curl http://platform.local/health
```

Swagger UI: http://platform.local/docs

### Data survives restarts

```cmd
kubectl rollout restart deployment platform-eng-app
curl http://platform.local/products/1
```

Data is still there because `/app/data` is on the `lab4-data` volume.

### Knowledge base and embeddings

The `knowledge\` folder is built into the image and loaded on startup. The API reaches Ollama on your PC through `host.docker.internal` for embeddings (see Troubleshooting if it cannot). Embedding settings and an OpenAI key can come from the optional `lab4-ai-env` secret created in section 12; after creating or changing it, run `kubectl rollout restart deployment platform-eng-app`.

```cmd
curl http://platform.local/knowledge/status
```

### Seeded user

```cmd
kubectl logs -l app=platform-eng-app
```

---

## 9. Using the product and PDF spec API

Set the base URL once for your terminal, then all commands work for any path:

```cmd
:: local (section 5) or Docker Compose (section 6)
set BASE=http://localhost:8121
:: or Kubernetes (section 8)
set BASE=http://platform.local
```

### 9.1 Products

```cmd
curl -X POST "%BASE%/products" -H "Content-Type: application/json" -d "{\"name\":\"Platform Component\",\"description\":\"Lab4 Container Artifact\",\"price\":299.00,\"stock\":5}"
curl %BASE%/products
curl %BASE%/products/1
curl -X PUT "%BASE%/products/1" -H "Content-Type: application/json" -d "{\"name\":\"Platform Component\",\"description\":\"Lab4 Container Artifact\",\"price\":299.00,\"stock\":8}"
```

### 9.2 PDF specification sheets

Each product can have several PDF spec sheets. On upload the API:

1. checks the file really is a PDF (by its signature, not its name) and is under 20 MB,
2. rejects the same PDF if it is already attached to that product,
3. stores the original under a random name in `data\uploads`,
4. extracts the text page by page,
5. splits it into overlapping **chunks** (~800 characters, never crossing a page) tagged with page numbers.

The chunks are what RAG will embed later, so answers can cite "spec page 2". For now, `/search` gives keyword search over them.

```cmd
:: upload the sample spec to product 1
curl -X POST "%BASE%/products/1/attachments" -F "file=@samples\platform-component-spec.pdf;type=application/pdf"

:: list a product's attachments
curl %BASE%/products/1/attachments

:: the chunks (text + page number)
curl %BASE%/attachments/1/chunks

:: keyword search across all specs (optionally &product_id=1)
curl "%BASE%/search?q=warranty"

:: download the original
curl -o downloaded-spec.pdf %BASE%/attachments/1/download

:: delete an attachment (also removes its chunks and file)
curl -X DELETE %BASE%/attachments/1
```

In PowerShell use `$BASE="http://localhost:8121"` and `curl.exe "$BASE/health"`.

### 9.3 Full API reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check (used by the probes) |
| POST | `/token` | Login (form: `username`, `password`) → JWT access token (section 11) |
| GET | `/users/me` | Current user (needs `Authorization: Bearer <token>`) |
| any | `/secure/products...` | Same product routes as below, but a valid token is required (section 11) |
| POST | `/products` | Create product (JSON: name, description, price, stock) |
| GET | `/products?skip=&limit=` | List products |
| GET | `/products/{id}` | Product with its attachments |
| PUT | `/products/{id}` | Replace product fields |
| DELETE | `/products/{id}` | Delete product, its attachments, chunks and files |
| POST | `/products/{id}/attachments` | Upload a PDF (multipart field `file`) |
| GET | `/products/{id}/attachments` | List a product's attachments |
| GET | `/attachments/{id}` | Attachment metadata and text preview |
| GET | `/attachments/{id}/download` | Original PDF |
| GET | `/attachments/{id}/chunks` | Chunks ready for embedding |
| DELETE | `/attachments/{id}` | Remove attachment, chunks and file |
| GET | `/search?q=&product_id=&limit=` | Keyword search over spec chunks (exact words) |
| GET | `/retrieve?q=&top_k=&product_id=&source=&doc_type=&min_score=` | **Retriever** for RAG: specs + knowledge by meaning, with citations (section 10) |
| GET | `/knowledge` | Knowledge documents (runbooks, postmortems, blueprints) |
| GET | `/knowledge/status` | Embedding provider and how many chunks have vectors |
| POST | `/knowledge/sync` | Re-read `knowledge\` (changed files only) and embed missing chunks |
| POST | `/knowledge/reembed` | Re-embed chunks after changing the embedding model |

Upload responses: `201` stored · `409` already attached · `413` too large · `415` not a PDF · `422` corrupt or encrypted. `status: "no_text"` means a scanned PDF with no text layer: it is stored but not searchable until OCR is added.

On startup the API seeds `admin` / `123456` (stored as a bcrypt hash). Log in with it through `POST /token` (section 11).

---

## 10. Knowledge base and RAG (Chapter 4)

Chapter 4 says an AI system needs three kinds of context. Lab 4 has all three:

| Context (Chapter 4) | In Lab 4 | Reached through |
|---|---|---|
| **Prompt context** | Instructions in `assistant/agent.py` and `assistant/rag.py` | The system prompt |
| **Organizational knowledge** | Product spec PDFs + `knowledge\` (runbooks, a postmortem, a blueprint) | **RAG**: `search_knowledge` → `/retrieve` |
| **Live platform context** | Current products, prices, stock, attachments | **MCP tools**: `list_products`, `get_product`, … |

The chapter's rule of thumb: **RAG for accumulated knowledge, MCP for live system state.**

### 10.1 The six RAG components in Lab 4

| # | Component | Lab 4 | Book's Lab 2 |
|---|---|---|---|
| 1 | Knowledge sources | Spec PDFs (uploaded) + Markdown in `knowledge\` | Markdown runbooks, postmortem, blueprints |
| 2 | Ingestion and embeddings | `pdf_utils.py` / `knowledge.py` chunking + `embeddings.py` (Ollama `nomic-embed-text`, or OpenAI) | Chunking + small CPU embedding model |
| 3 | Vector database | float32 vectors stored next to each chunk in SQLite (`db.py`) | Postgres + pgvector |
| 4 | Retriever | `GET /retrieve`: cosine similarity, keyword fallback (`knowledge_service.py`, `vectors.py`) | One cosine-distance query |
| 5 | LLM | OpenAI or Ollama, chosen in the UI | Amazon Bedrock / Anthropic API |
| 6 | Orchestration | Assistant **RAG mode** and `POST /api/ask` (`assistant/rag.py`) | FastAPI `/ask` |

Answers cite their sources with **citation, relevance score, last-updated date and owner**, as the chapter recommends.

### 10.2 The knowledge folder

```
knowledge\
├── _template.md                         standard format (files starting with _ are not loaded)
├── runbooks\                            crashloopbackoff, ingress-413-upload-too-large,
│                                        errimageneverpull, pvc-pending, ollama-unreachable
├── postmortems\2026-11-pdf-upload-413.md
└── blueprints\standard-service.md
```

Every document starts with the same metadata header (the chapter's "standardized knowledge types"):

```
---
title: Pod in CrashLoopBackOff
type: runbook            # runbook | postmortem | blueprint
owner: platform-team
last_updated: 2026-11-09
tags: [kubernetes, pod, crashloopbackoff]
---
```

Documents are split by heading (`## Fix`, `## Diagnose`, …), so a citation points to a section, for example `runbooks/crashloopbackoff.md > Pod in CrashLoopBackOff > Fix`.

**Synchronization** (the chapter's "knowledge synchronization pattern"): on startup, and on `POST /knowledge/sync`, the API reads `knowledge\`, skips files whose SHA-256 is unchanged, re-chunks changed files, removes deleted ones, and embeds every chunk that has no vector yet. Startup sync runs in the background, so the API is healthy immediately.

### 10.3 Embeddings

| `EMBEDDING_PROVIDER` | Model | Cost | Setup |
|---|---|---|---|
| `ollama` (default) | `nomic-embed-text` (768 dimensions) | Free | `ollama pull nomic-embed-text` |
| `openai` | `text-embedding-3-small` (1536 dimensions) | Paid | `OPENAI_API_KEY` in `.env` |
| `none` | – | – | Keyword search only |

- Spec PDFs are embedded during upload. If the provider is unreachable, the upload still succeeds and the chunks are embedded by the next sync.
- If no vectors are available, `/retrieve` uses **keyword** scoring and says so in its `mode` and `note` fields. Nothing breaks.
- **Changing the embedding model** changes the vectors (and usually their length). Run `POST /knowledge/reembed` or `POST /knowledge/sync` to re-embed everything with the new model. Vectors from another model are ignored until then.

### 10.4 Try it with curl

Set `BASE` as in section 9 (`http://localhost:8121` for local and Docker, `http://platform.local` for Kubernetes).

```cmd
:: embedding provider and how many chunks have vectors
curl %BASE%/knowledge/status

:: documents loaded from knowledge\
curl %BASE%/knowledge

:: the retriever: ranked chunks with citation, relevance_score, last_updated
curl "%BASE%/retrieve?q=Why+is+my+pod+in+CrashLoopBackOff&top_k=3"

:: only runbooks / only spec sheets / one product's specs
curl "%BASE%/retrieve?q=upload+rejected&source=knowledge&doc_type=runbook"
curl "%BASE%/retrieve?q=how+long+is+the+guarantee&source=specs"
curl "%BASE%/retrieve?q=memory+limit&product_id=1"

:: re-read knowledge\ and embed anything missing
curl -X POST %BASE%/knowledge/sync
```

`"mode": "vector"` means meaning-based search is working. Try `q=how long is the guarantee`: vector search should find the spec's **warranty** section even though the word "guarantee" never appears in it. Keyword search has no way to connect the two words.

Full RAG answer (retrieve, then the LLM answers with citations), like the book's `/ask`. This runs on the **assistant** (`http://localhost:8300` local and Docker, `http://assistant.platform.local` Kubernetes):

```cmd
curl -X POST "http://localhost:8300/api/ask" -H "Content-Type: application/json" -d "{\"question\":\"Why is my pod in CrashLoopBackOff?\"}"

curl -X POST "http://localhost:8300/api/ask" -H "Content-Type: application/json" -d "{\"question\":\"What warranty does Platform Component have?\",\"provider\":\"openai\",\"model\":\"gpt-4.1-mini\"}"
```

The response has `answer`, `sources` (with `relevance_score`, `last_updated`, `owner`) and `retrieval_mode`. Without `provider` and `model`, it uses `LLM_DEFAULT_PROVIDER` and `LLM_DEFAULT_MODEL` from `.env`.

### 10.5 Measure retrieval quality

The chapter asks for a regular evaluation loop. `scripts\rag_eval.py` asks 10 questions with known answers and reports the **hit rate** (expected document in the top k) and **MRR** (how high it ranks):

```cmd
python scripts\rag_eval.py
python scripts\rag_eval.py --base http://platform.local --k 3
```

Run it once with `EMBEDDING_PROVIDER=none` (keyword) and once with `ollama` (vector), and compare. On this small corpus keyword search already finds most documents; the difference shows in the ranking and on questions that use different words from the documents. Add your own questions to `CASES` in the script when you add documents.

### 10.6 RAG vs MCP in the assistant

Pick **RAG** in the Mode dropdown, or let **Agent** mode decide:

| Ask | Best answered by | Why |
|---|---|---|
| "Why is my pod in CrashLoopBackOff?" | RAG (runbook) | Accumulated knowledge |
| "What warranty does Platform Component have?" | RAG (spec PDF, page 2) | Organizational knowledge |
| "How many Platform Components are in stock right now?" | MCP (`list_products`) | Live state; a document would be stale |
| "Is the product with the 24-month warranty in stock?" | Agent: RAG **and** MCP | Needs both kinds of context |

In Agent mode, open **Show steps** to see the model choose `search_knowledge` for knowledge and `list_products` for live data.

### 10.7 Add your own knowledge

1. Copy `knowledge\_template.md` to, for example, `knowledge\runbooks\my-issue.md`, and fill in the header and sections.
2. Load it:
   - **Local:** `curl -X POST http://localhost:8121/knowledge/sync` (or restart the API).
   - **Docker Compose:** `knowledge\` is mounted into the container, so the same `curl` is enough.
   - **Kubernetes:** the folder is built into the image: `docker build -t lab4-fastapi:v2 .` then `kubectl rollout restart deployment platform-eng-app`.
3. Check with `curl %BASE%/knowledge` and ask the assistant about it.

---

## 11. Angular frontends: without and with login

Two Angular apps for teaching, both doing product CRUD against the same FastAPI API:

| | **v1** `frontend\angular-products-basic` | **v2** `frontend\angular-products-auth` |
|---|---|---|
| URL | http://localhost:4200 | http://localhost:4201 |
| API used | `/products` (open) | `/secure/products` (JWT required) |
| Login | none | `POST /token`, demo user `admin` / `123456` |
| Teaches | components, routing, signals, reactive forms, HttpClient service | v1 **plus** auth service, HTTP interceptor, route guard, 401 handling, return URL |

Both run at the same time, against the same data: create a product in v1 and it appears in v2 after signing in.

### 11.1 What the API adds for Angular

| Endpoint | Purpose |
|---|---|
| `POST /token` | Login with form fields `username` + `password` (OAuth2 password flow). Returns `access_token` (JWT), `expires_in`, `username` |
| `GET /users/me` | The user that a token belongs to |
| `GET/POST/PUT/DELETE /secure/products...` | Exactly the same product routes as `/products`, but every call needs `Authorization: Bearer <token>`; without it: `401` |

The product routes are written **once** (`products_router` in `app/main.py`) and mounted twice: open at `/products` and protected at `/secure/products` with a `get_current_user` dependency. Tokens are signed with `JWT_SECRET` and expire after `ACCESS_TOKEN_MINUTES` (60). The API allows browser calls from the Angular dev servers through **CORS** (`CORS_ORIGINS`, default `http://localhost:4200,http://localhost:4201`).

Try it with curl (`BASE` from section 9):

```cmd
:: no token: 401
curl -i %BASE%/secure/products

:: log in (form fields, not JSON)
curl -X POST %BASE%/token -d "username=admin&password=123456"

:: copy access_token from the answer into TOKEN, then
set TOKEN=paste-the-token-here
curl %BASE%/secure/products -H "Authorization: Bearer %TOKEN%"
curl %BASE%/users/me -H "Authorization: Bearer %TOKEN%"
```

In Swagger (`%BASE%/docs`), click **Authorize**, sign in as `admin` / `123456`, and the `/secure` routes work from the page. Paste a token into https://jwt.io to show students what is inside (`sub`, `iat`, `exp`): readable by anyone, but it cannot be changed without the secret.

### 11.2 Prerequisites

The apps use **Angular 22**, which needs **Node.js ^22.22.3, ^24.15.0 or ^26** and TypeScript 6.0 (installed by `npm install`).

```cmd
node --version
npm --version
```

Older Node (for example 20.x) fails during `npm install` or `ng serve`: install the current LTS from nodejs.org. The Angular CLI does not need a global install; the scripts use the project's local copy.

### 11.3 Run v1 (no login)

The API must be running (section 5, 6 or 8).

```cmd
cd C:\RajWork\2026\ESLearning\mcp\lab4-platform-engineering\frontend\angular-products-basic
npm install
npm start
```

Open http://localhost:4200: list, search, create, edit and delete products.

### 11.4 Run v2 (login first)

In a second terminal:

```cmd
cd C:\RajWork\2026\ESLearning\mcp\lab4-platform-engineering\frontend\angular-products-auth
npm install
npm start
```

Open http://localhost:4201. You land on **Sign in**; use `admin` / `123456`. The header shows who is signed in and when the session ends; **Sign out** clears the token.

Things to show in class:
- Open DevTools → Network: every `/secure/products` call carries `Authorization: Bearer …`; the `/token` call does not.
- DevTools → Application → Local Storage → `lab4.session`: delete it and refresh, and the guard sends you to Sign in.
- Set `ACCESS_TOKEN_MINUTES=1` in `.env`, restart the API, sign in, wait a minute and click Refresh: the API answers 401, the interceptor signs you out and returns you to Sign in with "Your session ended", then back to the page you were on.
- Call `/secure/products` with curl and no token: 401. The guard only hides pages; **the API is what enforces security**.

### 11.5 Point the apps at Docker or Kubernetes

Each app reads the API address from `src\environments\environment.ts`:

| API runs in | `apiUrl` |
|---|---|
| Local (`uvicorn`) or Docker Compose | `http://localhost:8121` (default) |
| Kubernetes | `http://platform.local` |

CORS needs no change for either: the API's default `CORS_ORIGINS` already allows `localhost:4200` and `localhost:4201`. If you serve the apps from another address, add it to `CORS_ORIGINS` (in `.env` for local and Docker; as an env entry in `k8s-local.yaml` for Kubernetes) and restart the API.

### 11.6 Code map

```
src\
├── environments\environment.ts    apiUrl
├── main.ts                        bootstrapApplication(App, appConfig)
└── app\
    ├── app.config.ts              providers: router (with component input binding), HttpClient (+ interceptor in v2)
    ├── app.routes.ts              routes (v2: login route + guarded children)
    ├── app.ts / app.html          header + <router-outlet>
    ├── products\
    │   ├── product.model.ts       Product, ProductInput
    │   ├── product.service.ts     all HTTP calls (v1: /products, v2: /secure/products)
    │   ├── product-list.*         table, search (signal + computed), delete
    │   └── product-form.*         create/edit with reactive form validation; :id arrives as an input()
    ├── shared\error-message.ts    turns FastAPI errors into readable messages
    └── auth\                      v2 only
        ├── auth.service.ts        login (POST /token as a form), session signal, localStorage, logout
        ├── auth.interceptor.ts    adds the Bearer token; on 401 signs out and redirects
        ├── auth.guard.ts          CanActivateFn: no session -> /login?returnUrl=...
        └── login.*                sign-in form; returnUrl and reason arrive as input()s
```

Angular 22 makes **OnPush** change detection the default and runs **zoneless**, so all component state is in **signals** (`signal`, `computed`, `input`). That is why the code never mutates plain fields for the view.

### 11.7 Exercises

1. **Register:** add `POST /register` to the API and a sign-up page to v2.
2. **Roles:** add a `role` column; only `admin` may delete (check it in the API, then hide the button in Angular).
3. **Spec sheets:** show a product's PDF attachments in the edit page (`GET /secure/...` would need the attachment routes on the secure router too).
4. **Signal Forms:** Angular 22's stable `form()` API: rewrite `product-form.ts` with it and compare with Reactive Forms.
5. **httpResource:** replace the list's manual `subscribe` with `httpResource` and its `isLoading` / `error` signals.
6. **Close the open door:** set `OPEN_PRODUCT_API=false`. v1 stops working (404) and so do the MCP server and assistant. Discuss how the MCP server should get its own credentials.

---

## 12. Kubernetes: deploy the AI layer

Do section 8 first; the AI layer calls `platform-eng-service`.

```cmd
cd C:\RajWork\2026\ESLearning\mcp\lab4-platform-engineering
docker build -f Dockerfile.ai -t lab4-ai:v1 .
kubectl create secret generic lab4-ai-env --from-env-file=.env
kubectl apply -f k8s-ai.yaml
kubectl get pods
kubectl get ingress
```

`lab4-mcp-server-…` and `lab4-assistant-…` should be `1/1 Running`. Open http://assistant.platform.local.

Create the secret **before** applying `k8s-ai.yaml`; otherwise the pods show `CreateContainerConfigError` until it exists.

In the cluster:
- The assistant uses **streamable HTTP** to `http://lab4-mcp-service/mcp`. To use stdio inside the pod instead, change `MCP_TRANSPORT` in `k8s-ai.yaml` to `stdio`.
- Ollama on your PC is reached through `http://host.docker.internal:11434/v1`. If it is not reachable, see Troubleshooting.
- `k8s-ai.yaml` overrides the `.env` values that must differ in the cluster (`API_BASE_URL`, `PUBLIC_API_URL`, `MCP_URL`, `OLLAMA_BASE_URL`).

Test the in-cluster MCP server from your PC (venv active):

```cmd
python scripts\mcp_smoke_test.py --transport http --url http://mcp.platform.local/mcp
```

After changing `.env`:

```cmd
kubectl delete secret lab4-ai-env
kubectl create secret generic lab4-ai-env --from-env-file=.env
kubectl rollout restart deployment lab4-assistant lab4-mcp-server
```

---

## 13. .env reference

| Variable | Default in `.env.example` | Meaning |
|---|---|---|
| `LLM_PROVIDERS` | `ollama,openai` | Providers shown in the UI |
| `LLM_DEFAULT_PROVIDER` | `ollama` | Pre-selected provider |
| `LLM_DEFAULT_MODEL` | `qwen2.5:7b` | Pre-selected model |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama's OpenAI-compatible endpoint (Kubernetes overrides to `host.docker.internal`) |
| `OLLAMA_MODELS` | `qwen2.5:7b,llama3.2` | Models listed for Ollama (pulled models are added automatically) |
| `OLLAMA_TEMPERATURE` | `0.1` | Low = more reliable tool use |
| `OPENAI_API_KEY` | *(empty)* | Paid API key; OpenAI stays disabled in the UI until set |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint works |
| `OPENAI_MODELS` | `gpt-4.1-mini,gpt-5-mini` | Models listed for OpenAI |
| `LLM_TIMEOUT_SECONDS` | `300` | How long to wait for a model |
| `JWT_SECRET` | demo value | Signs login tokens. Use a long random value (32+ characters); changing it logs everyone out |
| `ACCESS_TOKEN_MINUTES` | `60` | Token lifetime |
| `CORS_ORIGINS` | `http://localhost:4200,http://localhost:4201` | Browser apps allowed to call the API |
| `OPEN_PRODUCT_API` | `true` | `false` removes the open `/products` routes (Angular v1, MCP server and assistant stop working) |
| `EMBEDDING_PROVIDER` | `ollama` | Embeddings for RAG: `ollama` (free), `openai` (paid) or `none` (keyword search only) |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model; OpenAI default is `text-embedding-3-small`. Re-embed after changing it |
| `EMBEDDING_BASE_URL` | *(unset)* | Optional: embeddings endpoint if different from `OLLAMA_BASE_URL` / `OPENAI_BASE_URL` |
| `RAG_TOP_K` | `5` | Passages the assistant's RAG mode puts into the prompt |
| `KNOWLEDGE_DIR` | `.\knowledge` | Optional: folder of Markdown knowledge documents |
| `KNOWLEDGE_SYNC_ON_STARTUP` | `true` | Optional: load and embed `knowledge\` when the API starts |
| `AGENT_MAX_STEPS` | `6` | Default step budget for Agent mode |
| `APPROVAL_TOOLS` | create/update/delete/upload tools | Tools that pause for Approve/Reject |
| `MCP_TRANSPORT` | `stdio` | How the assistant reaches the MCP server: `stdio` or `streamable_http` |
| `MCP_URL` | `http://localhost:8200/mcp` | Used when `MCP_TRANSPORT=streamable_http` (Kubernetes from Windows: `http://mcp.platform.local/mcp`; Docker and Kubernetes set their own internal value) |
| `API_BASE_URL` | `http://localhost:8121` | Where the MCP server finds the product API (API in Kubernetes: `http://platform.local`) |
| `PUBLIC_API_URL` | `http://localhost:8121` | Base of download links shown to users |
| `MAX_UPLOAD_MB` | `20` | Upload limit in the MCP server |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8200` | Optional: defaults for the MCP HTTP server |
| `MCP_ALLOWED_HOSTS` | localhost, mcp.platform.local, lab4-mcp-service (Compose adds `mcp`) | Optional: Host headers the MCP HTTP server accepts (DNS-rebinding protection) |
| `MCP_ALLOWED_ORIGINS` | localhost, assistant.platform.local | Optional: browser origins the MCP HTTP server accepts |

The product API, the assistant, the MCP server and the scripts all read `.env` from the project folder. Real environment variables (such as those set in `docker-compose.yml`, `k8s-local.yaml` or `k8s-ai.yaml`) take priority. The API uses only the embedding settings (and `OLLAMA_BASE_URL` / `OPENAI_API_KEY` for them); its database and file settings come from its own defaults or the deployment files.

Switching provider and model is done **in the UI** (header dropdowns); the choice is remembered in your browser.

---

## 14. The MCP server

`mcp_server/server.py` wraps every API endpoint as an MCP tool. The API stays the source of truth; MCP gives any AI host a standard way to call it.

| Tool | API endpoint | 🔒 Needs approval in the assistant |
|---|---|---|
| `health_check` | `GET /health` | |
| `list_products` | `GET /products` | |
| `get_product` | `GET /products/{id}` (with attachments) | |
| `create_product` | `POST /products` | 🔒 |
| `update_product` | `PUT /products/{id}` (only the fields given change) | 🔒 |
| `delete_product` | `DELETE /products/{id}` | 🔒 |
| `list_product_attachments` | `GET /products/{id}/attachments` | |
| `get_attachment` | `GET /attachments/{id}` + download link | |
| `get_attachment_chunks` | `GET /attachments/{id}/chunks` (optionally one page) | |
| `search_product_specs` | `GET /search` (exact keywords) | |
| `search_knowledge` | `GET /retrieve` (by meaning, specs + runbooks, with citations) | |
| `list_knowledge_documents` | `GET /knowledge` | |
| `upload_product_attachment` | `POST /products/{id}/attachments` (file path or base64) | 🔒 |
| `delete_attachment` | `DELETE /attachments/{id}` | 🔒 |

### Transports

| | stdio | streamable HTTP |
|---|---|---|
| How it works | The host starts the server as a child process and talks over stdin/stdout | The server is a web service; hosts connect to a URL |
| Command | `python -m mcp_server.server --transport stdio` | `python -m mcp_server.server --transport streamable-http --port 8200` |
| URL | none | `http://localhost:8200/mcp` (local and Docker) or `http://mcp.platform.local/mcp` (Kubernetes) |
| Best for | One user on one machine (Claude Desktop, local dev) | A shared server for many users and hosts |
| Uploading a PDF | `file_path` works (same machine) | `file_path` only if the server runs on your PC; otherwise send `pdf_base64` or use the assistant's upload button |
| Test with | `python scripts\mcp_smoke_test.py` | `python scripts\mcp_smoke_test.py --transport http` |

Command-line options: `--transport stdio|streamable-http`, `--host` (default `127.0.0.1`; use `0.0.0.0` to accept connections from other machines), `--port` (default `8200`).

The HTTP server runs **stateless** (no sticky sessions needed, so it can scale out) and has **DNS-rebinding protection**: only the hosts in `MCP_ALLOWED_HOSTS` are accepted.

The server uses the MCP Python SDK **1.x** (`mcp>=1.28,<2`). SDK 2.0 renamed `FastMCP` and rewrote the client, so don't upgrade without migrating.

---

## 15. Use the MCP server from Claude Desktop and other hosts

**Claude Desktop (stdio):** needs the venv from section 4.1.

1. Open Claude Desktop → Settings → Developer → Edit Config.
2. Merge in the `mcpServers` block from `claude_desktop_config.example.json`. Fix the paths if your folder differs.
3. `API_BASE_URL` in that block is `http://localhost:8121`, which works when the API runs locally (section 5.1) or in Docker Compose (section 6). If your API runs in Kubernetes, change both URLs to `http://platform.local`.
4. Save and restart Claude Desktop. The 14 tools appear in Claude's tool menu. Claude Desktop asks for its own permission before running tools.

**Claude Code (streamable HTTP):**

```cmd
:: local (section 5.4) or Docker Compose (section 6)
claude mcp add --transport http lab4-products http://localhost:8200/mcp
:: or Kubernetes
claude mcp add --transport http lab4-products http://mcp.platform.local/mcp
```

**VS Code and other MCP hosts:** add an HTTP MCP server with the same URL.

This is the point of MCP: build the tools once, then use them from any host.

---

## 16. What is agentic AI? (with this lab)

The assistant has three **modes** (header dropdown) so the difference is visible:

| Mode | What happens | Can it see your data? |
|---|---|---|
| **Chat only** | One LLM call. The model answers from what it learned in training. | No |
| **RAG** | The host retrieves passages (specs + runbooks), then one LLM call answers from them with citations. No tool choice by the model. | Documents only |
| **Assistant** | The model may call tools **once**, then must answer. | One look |
| **Agent** | The model works in a **loop** until the job is done, within the step budget. | As many looks as it needs |

An **agent** is an LLM given a goal, tools and a loop. Each turn it **reasons** ("I need the product id first"), **acts** (calls a tool), **observes** (reads the result), and repeats. This is the **ReAct** pattern; the code is `assistant/agent.py`.

Example: *"Increase the stock of Platform Component by 3"*

```
🧠 I need the product's id and current stock.
🔧 list_products                     → [{id:1, name:"Platform Component", stock:5, ...}]
🔧 update_product {id:1, stock:8}    ✋ waits for your approval
✋ approved
📄 {id:1, stock:8, ...}
💬 "Done: Platform Component stock is now 8 (was 5)."
```

Nobody scripted "find the id, add 3, update". The model planned it. That is what makes it agentic. Compare with `scripts\mcp_smoke_test.py`, where a person wrote every tool call in a fixed order.

### Guardrails (enforced by the platform, not the model)

| Guardrail | Where | Why |
|---|---|---|
| **Step budget** (`AGENT_MAX_STEPS`, UI "Max steps") | `agent.py` | Stops runaway loops and cost. When reached, the model must answer without tools |
| **Human approval** (`APPROVAL_TOOLS`) | `agent.py` + UI card | Create, update, delete and upload pause for Approve/Reject. Read-only tools run freely |
| **Self-correction** | `agent.py` | Bad tool arguments and API errors go back to the model as messages so it can fix its call |
| **Visible trace** | UI "Show agent steps" | Every thought, tool call and result is shown: an audit trail |
| **Tools wrap the API** | `mcp_server/` | The agent can only do what the API allows; it never touches the database |

The **Attach a spec PDF** button in the sidebar is a *button-triggered workflow*: it calls the upload tool directly with no prompt, as described in Chapter 2 of the book.

---

## 17. Demo script for class

Works the same locally or in Docker (http://localhost:8300) and in Kubernetes (http://assistant.platform.local).

1. **Chat only** → "What warranty does Platform Component have?" It doesn't know, or invents something.
2. **Assistant** → same question. One tool call (search), then an answer.
3. **Agent** → "Which products have spec sheets, and what is the warranty on each? Cite the page." Watch it loop, then open **Show agent steps**.
4. **Agent** → "Create a product called Edge Gateway, price 150, stock 10". The approval card appears. **Reject**, then ask again and **Approve**.
5. Switch **llama3.2 → qwen2.5:7b → OpenAI** and compare tool choices, speed and answer quality.
6. Upload `samples\platform-component-spec.pdf` to a new product with the **Attach a spec PDF** button, then ask about it.
7. **RAG** → "Why is my pod in CrashLoopBackOff?" The answer cites the runbook sections with scores and dates. Then ask "How many are in stock?" in RAG mode: documents can't answer live questions.
8. **Agent** → "Is the product with the 24-month warranty in stock?" It uses `search_knowledge` (RAG) **and** `list_products` (MCP): Chapter 4's point in one question.
9. Run `python scripts\rag_eval.py` with `EMBEDDING_PROVIDER=none` and then `ollama` to compare keyword and vector retrieval.
10. Run `python scripts\mcp_smoke_test.py` to show the same tools driven by a fixed script instead of an agent.
11. Open Angular **v1** (http://localhost:4200) and **v2** (http://localhost:4201) side by side: the same products, but v2 needs a login. Show the `Authorization` header in DevTools and a `401` from curl without a token (section 11.4).

On a laptop CPU, a local-model agent turn can take 30–90 seconds; OpenAI answers in a few seconds.

---

## 18. Updating after code changes

| Changed | Local (no Docker) | Docker Compose | Kubernetes |
|---|---|---|---|
| `app\` | auto-reloads with `--reload` | `docker compose up -d --build api` | `docker build -t lab4-fastapi:v2 .` then `kubectl rollout restart deployment platform-eng-app` |
| `assistant\` or `mcp_server\` | assistant auto-reloads; MCP stdio is picked up on the next request; restart the MCP HTTP server (terminal 3) | `docker compose up -d --build mcp assistant` | `docker build -f Dockerfile.ai -t lab4-ai:v1 .` then `kubectl rollout restart deployment lab4-assistant lab4-mcp-server` |
| `requirements*.txt` | `pip install -r requirements.txt -r requirements-ai.txt` | `docker compose up -d --build` | rebuild the image, then restart |
| `.env` | restart the assistant and the MCP HTTP server | `docker compose up -d --force-recreate mcp assistant` | recreate the secret (section 12) and restart both AI deployments |
| `knowledge\*.md` | `curl -X POST http://localhost:8121/knowledge/sync` | `curl -X POST http://localhost:8121/knowledge/sync` (folder is mounted) | `docker build -t lab4-fastapi:v2 .` then `kubectl rollout restart deployment platform-eng-app` |
| `EMBEDDING_MODEL` | restart the API, then `POST /knowledge/reembed` | `docker compose up -d --force-recreate api`, then `POST /knowledge/reembed` | update the secret, restart `platform-eng-app`, then `POST /knowledge/reembed` |
| `frontend\*\src\` | `npm start` reloads the browser automatically | same (the Angular apps run on your PC) | same |
| `docker-compose.yml` | n/a | `docker compose up -d` | n/a |
| `k8s-*.yaml` | n/a | n/a | `kubectl apply -f <file>` |

Docker Desktop's Kubernetes uses your local images directly (`imagePullPolicy: Never`), so no registry push is needed.

---

## 19. Troubleshooting

### Local (no Docker)

| Symptom | Fix |
|---|---|
| `'uvicorn' is not recognized` / `No module named …` | Activate the venv (`.venv\Scripts\activate`) and run `pip install -r requirements.txt -r requirements-ai.txt` |
| `No module named 'app'` / `'assistant'` / `'mcp_server'` | Run commands from the project folder, not from inside a subfolder |
| `[Errno 10048] … address already in use` | Another process uses the port: `netstat -ano \| findstr :8121` (or 8200, 8300), then stop it or pick another `--port` |
| Smoke test: "Cannot reach the product API" | Start the API (5.1) and check `API_BASE_URL` in `.env` is `http://localhost:8121` |
| Smoke test over HTTP: connection refused | Start the MCP HTTP server (5.4) |
| `python -m mcp_server.server --transport stdio` does nothing | Normal: it waits for an MCP host. Use the smoke test or the Inspector |
| PowerShell blocks `Activate.ps1` | `Set-ExecutionPolicy -Scope Process Bypass`, then activate again |
| `python` opens the Microsoft Store | Install Python from python.org, or use `py -3.11 -m venv .venv` |

### Docker Compose

| Symptom | Fix |
|---|---|
| `docker: 'compose' is not a docker command` | Update Docker Desktop (Compose v2 is built in) |
| `env_file … unsupported` / `path` error | Docker Compose is older than 2.24: update Docker Desktop, or replace the `env_file` block with `env_file: .env` and create `.env` first |
| `Bind for 127.0.0.1:8121 failed: port is already allocated` | A local `uvicorn` (section 5) or another container uses the port. Stop it, or change the left-hand port in `docker-compose.yml` |
| `mcp` keeps waiting / `dependency failed to start` | API not healthy: `docker compose logs api` |
| Assistant: "Ollama not reachable" | Same fix as Kubernetes below (`OLLAMA_HOST=0.0.0.0`, restart Ollama) |
| Code changes not visible | Rebuild: `docker compose up -d --build` |
| Need a clean start | `docker compose down -v` (deletes data), then `docker compose up -d --build` |

### Angular apps

| Symptom | Fix |
|---|---|
| `npm install` fails with `EBADENGINE` / `Unsupported engine` | Node.js too old for Angular 22: install Node 22.22.3+ or 24.15+ (`node --version`) |
| `'ng' is not recognized` / `Cannot find module '@angular/build'` | Run `npm install` in that app's folder first, then `npm start` |
| Page shows "Cannot reach the API at …" | API not running, wrong `apiUrl` in `src\environments\environment.ts`, or a CORS block: check the browser console for `CORS` and add the page's address to `CORS_ORIGINS` |
| Port 4200 / 4201 already in use | Stop the other `ng serve`, or `npm start -- --port 4300` and add `http://localhost:4300` to `CORS_ORIGINS` |
| v2: "401: Incorrect username or password" | Use `admin` / `123456`; the user is seeded when the API starts with an empty database |
| v2: sent back to Sign in after every action | `JWT_SECRET` changed between login and request (API restarted with a new value), or the PC clock is far off: sign in again |
| v1: "404: Not Found" | `OPEN_PRODUCT_API=false` in the API's settings: set it back to `true` |
| Build fails after an Angular update with errors in `angular.json` / `tsconfig.json` | Create a fresh project with `npx @angular/cli@22 new my-app --style=css --ssr=false --routing`, then copy `src\app` and `src\environments` into it |

### Kubernetes: product API

| Symptom | Fix |
|---|---|
| `ping platform.local` fails | Hosts file not saved as Administrator; run `ipconfig /flushdns` |
| Connection refused on port 80 | Controller not running (7.3) or port 80 used by another program (7.2) |
| 404 from nginx | Ingress host must be exactly `platform.local`: `kubectl describe ingress platform-eng-ingress` |
| 503 from nginx | App pod not Ready: `kubectl get pods`, `kubectl logs -l app=platform-eng-app` |
| 413 from nginx on upload | `proxy-body-size` annotation missing; re-apply `k8s-local.yaml` |
| Pod stuck `Pending` | PVC not bound: `kubectl describe pvc lab4-data` |
| `ErrImageNeverPull` | Build the image first: `docker build -t lab4-fastapi:v2 .` |

### AI Assistant and MCP (both paths)

| Symptom | Fix |
|---|---|
| "Ollama not reachable" (local run) | Start Ollama (tray icon); check `ollama list` |
| "Ollama not reachable" (Kubernetes) | Ollama listens on 127.0.0.1 by default. Set a Windows environment variable `OLLAMA_HOST=0.0.0.0`, restart Ollama, and allow port 11434 if Windows Firewall asks |
| "Model … was not found" | `ollama pull <model>` |
| Model answers without using tools, or picks wrong tools | Use Agent mode and a bigger model (`qwen2.5:7b` or OpenAI); `llama3.2` 3B is unreliable at tool use |
| OpenAI shown as unavailable | `OPENAI_API_KEY` empty: set it in `.env` (and recreate the secret in Kubernetes) |
| "OpenAI rejected the API key" / rate limit | Check the key and that the API account has billing credit |
| Tools panel: "Cannot connect to the MCP server" | stdio: install `requirements-ai.txt` in the same venv. HTTP: check the server is running and `MCP_URL` is right |
| HTTP 421 or "Invalid Host header" from the MCP server | Add your host name to `MCP_ALLOWED_HOSTS` in `.env` |
| Tool results say "Cannot reach the product API" | `API_BASE_URL` wrong, or the API is not running |
| `/retrieve` returns `"mode": "keyword"` | No vectors yet. `ollama pull nomic-embed-text`, make sure the API can reach Ollama (`/knowledge/status` shows `last_error`), then `POST /knowledge/sync` |
| `/knowledge/status` shows `HTTP 404 … model not found` | Pull the embedding model: `ollama pull nomic-embed-text` |
| `"knowledge_documents": 0` | `knowledge\` not found: run the API from the project folder, check `KNOWLEDGE_DIR`, or rebuild the image (Kubernetes) |
| RAG answers "I found nothing relevant" | Check `/retrieve?q=…` directly; add or improve the runbook, or ask with words closer to the documents |
| Retrieval quality dropped after changing models | Vectors from the old model are ignored: `POST /knowledge/reembed` |
| AI pods in `CreateContainerConfigError` | Secret `lab4-ai-env` missing: create it (section 12) |
| 504 from nginx on long answers | Model too slow (Ingress allows 300 s). Use a smaller model or OpenAI |
| Claude Desktop does not show the tools | Check the paths in its config point to `.venv\Scripts\python.exe` and `mcp_server\server.py`, and that the API in `API_BASE_URL` is running; see Claude Desktop's MCP logs |

Useful Kubernetes commands:

```cmd
kubectl get pods
kubectl logs -l app=platform-eng-app
kubectl logs -l app=lab4-mcp-server
kubectl logs -l app=lab4-assistant
kubectl describe pod <pod-name>
```

---

## 20. Clean up

**Local:** press Ctrl+C in each terminal. Delete the `data` folder to remove local data, and `.venv` to remove the Python packages.

**Docker Compose:**

```cmd
:: stop, keep data
docker compose down
:: stop and delete the data volume
docker compose down -v
```

If you used plain `docker run` (6.6): `docker rm -f lab4-api` and `docker volume rm lab4-data-manual`.

**Kubernetes:**

```cmd
kubectl delete -f k8s-ai.yaml
kubectl delete secret lab4-ai-env
kubectl delete -f k8s-local.yaml
kubectl delete -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/cloud/deploy.yaml
```

Deleting `k8s-local.yaml` also deletes the `lab4-data` volume and everything in it. Remove the hosts-file lines if you no longer need them.

---

## 21. Limitations and security notes

- **For learning only.** Credentials (`admin`/`123456`, `jwt_secret`) are demo values. Never reuse them.
- `/secure/products` requires a JWT, but the open `/products` routes (plus attachments, search and knowledge) stay **unauthenticated** on purpose, for Angular v1, the MCP server and the assistant. Set `OPEN_PRODUCT_API=false` to close the product routes; protecting the rest is an exercise.
- Login has **no rate limiting, refresh tokens or roles**, and Angular v2 keeps the token in `localStorage` (readable by any script on the page). Fine for teaching; production apps add these protections and often use httpOnly cookies.
- **SQLite** means a single API replica. Switch `DATABASE_URL` to PostgreSQL to scale out.
- Assistant conversations live **in memory**: a restart clears them, and the assistant runs as one replica.
- Vector search is **brute force** over vectors stored in SQLite: fine for thousands of chunks. At scale, use a vector index such as pgvector (as in the book) or OpenSearch.
- Retrieval is **not permission-aware**: every user can retrieve every runbook and spec. Chapter 4 requires identity, document-level access control, metadata filters and audit logs before production use.
- RAG evaluation covers retrieval hit rate only (`scripts\rag_eval.py`); answer faithfulness is checked by reading the citations.
- Scanned PDFs need **OCR** before they are searchable.
- The approval gate is enforced by **this assistant**. Other MCP hosts use their own tool-permission prompts, so the API itself still needs auth before real use.
- The local MCP HTTP server listens on `127.0.0.1` only. Use `--host 0.0.0.0` only on a trusted network.
- Upstream **ingress-nginx was retired in March 2026** and gets no more security fixes. It's fine for a local lab; for anything real use Gateway API or another maintained Ingress controller.
