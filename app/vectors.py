"""Vector helpers and keyword scoring. Pure Python, no numpy needed at lab scale.

Embeddings are stored in SQLite as float32 bytes. Search is brute force: compare the
question vector with every stored chunk vector. That is fine for thousands of chunks;
a real platform would use a vector index such as pgvector (as in the book) or OpenSearch.
"""
import math
import re
from array import array
from typing import List, Optional, Sequence

STOPWORDS = set("""a an and are as at be but by can do does for from has have how i if in into is it its
me my of on or our so that the their them then there these this to was what when where which
who why will with you your we us should could would please""".split())


def pack(vector: Sequence[float]) -> bytes:
    return array("f", vector).tobytes()


def unpack(blob: Optional[bytes]) -> Optional[List[float]]:
    if not blob:
        return None
    values = array("f")
    values.frombytes(blob)
    return values.tolist()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9_.\-]*", text.lower()) if t not in STOPWORDS and len(t) > 1]


def keyword_score(query: str, text: str) -> float:
    """Share of the query's distinct terms found in the text (0..1), with a small bonus for repeats."""
    q = set(tokens(query))
    if not q:
        return 0.0
    words = tokens(text)
    if not words:
        return 0.0
    vocab = set(words)
    matched = [t for t in q if t in vocab or any(w.startswith(t) for w in vocab)]
    if not matched:
        return 0.0
    coverage = len(matched) / len(q)
    density = min(sum(words.count(t) for t in matched) / max(len(words), 1) * 10, 0.2)
    return round(min(coverage * 0.8 + density, 1.0), 4)


def rank(question: str, items: List[dict], query_vector: Optional[List[float]], top_k: int,
         min_score: float = 0.0) -> dict:
    """Score candidates by cosine similarity when vectors exist, otherwise by keyword overlap."""
    embedded = [i for i in items if i["_vector"] is not None]
    if query_vector is not None and embedded:
        mode = "vector"
        scored = [(cosine(query_vector, i["_vector"]), i) for i in embedded]
    else:
        mode = "keyword"
        scored = [(keyword_score(question, i["_match_text"]), i) for i in items]
        scored = [s for s in scored if s[0] > 0]
    scored = [s for s in scored if s[0] >= min_score]
    scored.sort(key=lambda s: s[0], reverse=True)

    results = []
    for position, (score, item) in enumerate(scored[:top_k], start=1):
        clean = {k: v for k, v in item.items() if not k.startswith("_")}
        clean.update(rank=position, relevance_score=round(float(score), 4))
        results.append(clean)
    return {"mode": mode, "coverage": {"embedded": len(embedded), "total": len(items)}, "results": results}
