"""Measure retrieval quality: for each question, is the expected document in the top-k results?

Chapter 4 asks for a regular evaluation loop for RAG (retrieval hit rate, freshness, faithfulness).
This script covers the first part: hit rate and mean reciprocal rank (MRR) of the retriever.

    python scripts/rag_eval.py                              # API at API_BASE_URL or http://localhost:8121
    python scripts/rag_eval.py --base http://platform.local --k 3

Add your own cases to CASES below whenever you add knowledge documents.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

# (question, part of the expected source citation). Several questions avoid the documents'
# exact words on purpose: keyword search tends to miss them, vector search should not.
CASES = [
    ("Why is my pod in CrashLoopBackOff?", "runbooks/crashloopbackoff.md"),
    ("My container keeps restarting right after it starts", "runbooks/crashloopbackoff.md"),
    ("PDF upload fails with 413 request entity too large", "ingress-413-upload-too-large.md"),
    ("Big spec documents are rejected by the gateway", "413"),
    ("ErrImageNeverPull after kubectl apply", "runbooks/errimageneverpull.md"),
    ("Kubernetes cannot find my locally built image", "runbooks/errimageneverpull.md"),
    ("PersistentVolumeClaim pending, pod not scheduled", "runbooks/pvc-pending.md"),
    ("The assistant cannot talk to the local language model", "runbooks/ollama-unreachable.md"),
    ("What probes and resource limits must every service have?", "blueprints/standard-service.md"),
    ("What went wrong with uploads after the ingress was introduced?", "postmortems/2026-11-pdf-upload-413.md"),
]


def retrieve(base: str, question: str, k: int) -> dict:
    url = f"{base.rstrip('/')}/retrieve?" + urllib.parse.urlencode({"q": question, "top_k": k, "source": "knowledge"})
    with urllib.request.urlopen(url, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieval hit rate for the Lab 4 knowledge base")
    parser.add_argument("--base", default=os.getenv("API_BASE_URL", "http://localhost:8121"))
    parser.add_argument("--k", type=int, default=3)
    args = parser.parse_args()

    hits, rr, mode = 0, 0.0, None
    print(f"API {args.base}  top-{args.k}\n")
    for question, expected in CASES:
        data = retrieve(args.base, question, args.k)
        mode = data.get("mode")
        sources = [r.get("citation", "") for r in data.get("results", [])]
        rank = next((i for i, s in enumerate(sources, start=1) if expected in s), None)
        hits += rank is not None
        rr += 1 / rank if rank else 0
        top = f"{sources[0][:60]} ({data['results'][0]['relevance_score']})" if sources else "(no results)"
        print(f"{'HIT ' if rank else 'MISS'} rank={rank or '-'}  {question[:58]:58}  top: {top}")

    n = len(CASES)
    print(f"\nRetrieval mode: {mode}")
    print(f"Hit rate@{args.k}: {hits}/{n} = {hits / n:.0%}    MRR: {rr / n:.2f}")
    if mode == "keyword":
        print("Keyword mode: pull nomic-embed-text and run POST /knowledge/sync, then compare the vector score.")
    sys.exit(0 if hits == n else 1)


if __name__ == "__main__":
    main()
