"""Classic RAG (Chapter 4): retrieve, then generate. One LLM call, no agent loop.

    question --> retriever (search_knowledge MCP tool -> API /retrieve) --> top chunks
             --> prompt = instructions + numbered context passages + question
             --> LLM --> answer with [n] citations + the source list

Compare with Agent mode, where the model decides for itself when and what to search.
"""
from typing import Awaitable, Callable, List, Optional

RAG_SYSTEM_PROMPT = """You answer questions about the Lab 4 platform and its products using ONLY the numbered context passages provided.

Rules:
- Base every statement on the passages. Cite them inline like [1] or [2][3].
- If the passages do not contain the answer, say so plainly and suggest what to check; do not guess.
- Be concise. Use short steps or bullet points for procedures, and keep commands exactly as written in the passages."""

Searcher = Callable[[str, int], Awaitable[dict]]


def build_context(results: List[dict], max_chars: int = 6000) -> str:
    parts, used = [], 0
    for i, r in enumerate(results, start=1):
        header = f"[{i}] {r.get('title', '')} | {r.get('citation', '')} | last updated {r.get('last_updated', '?')}"
        block = f"{header}\n{r.get('text', '').strip()}"
        if used + len(block) > max_chars and parts:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def sources_from(results: List[dict]) -> List[dict]:
    keys = ("title", "citation", "source_type", "relevance_score", "last_updated", "product_id", "attachment_id", "owner")
    return [{"ref": i, **{k: r.get(k) for k in keys if r.get(k) is not None}} for i, r in enumerate(results, start=1)]


async def answer_with_rag(llm, model: str, question: str, search: Searcher, top_k: int = 5,
                          temperature: Optional[float] = None) -> dict:
    retrieved = await search(question, top_k)
    results = retrieved.get("results", [])
    base = {"retrieval_mode": retrieved.get("mode"), "coverage": retrieved.get("coverage"),
            "note": retrieved.get("note"), "sources": sources_from(results)}
    if not results:
        return {**base, "answer": "I found nothing relevant in the product specs or the knowledge base for that question."}

    messages = [
        {"role": "system", "content": RAG_SYSTEM_PROMPT},
        {"role": "user", "content": f"Context passages:\n\n{build_context(results)}\n\nQuestion: {question}"},
    ]
    kwargs = {"model": model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    resp = await llm.chat.completions.create(**kwargs)
    return {**base, "answer": resp.choices[0].message.content or ""}
