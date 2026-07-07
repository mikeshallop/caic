"""cAIc — Query triage and cluster node selection."""
import logging

import httpx

from config import TRIAGE_BASE, TRIAGE_TIMEOUT, LLAMA_SERVER_BASE

log = logging.getLogger("caic")

_CLASSIFICATION_PROMPT = """Classify the following user query into exactly one category. Respond with only the category name.

Categories:
- general: everyday questions, chitchat, creative writing, advice, explanations
- code: programming, debugging, code generation, technical questions about software
- search: questions about current events, real-time information, weather, news, specific things that may have changed since training
- rag: questions about specific documents, personal data, notes, memory, uploaded content

Query: {query}
Category:"""


async def classify_query(query: str) -> str:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TRIAGE_BASE}/chat/completions",
                json={
                    "model": "phi-4-mini",
                    "messages": [
                        {"role": "system", "content": "You are a query classifier. Respond with exactly one word."},
                        {"role": "user", "content": _CLASSIFICATION_PROMPT.format(query=query)},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 10,
                },
                timeout=TRIAGE_TIMEOUT,
            )
            text = resp.json()["choices"][0]["message"]["content"].strip().lower()
            valid = {"general", "code", "search", "rag"}
            for v in valid:
                if v in text:
                    return v
    except Exception:
        log.warning("triage classify_query failed, falling back to general", exc_info=True)
    return "general"


def select_node(classification: str) -> dict | None:
    from cluster import CLUSTER_NODES

    for node in CLUSTER_NODES.values():
        if node.get("status") != "active":
            continue
        am = node.get("active_model") or {}
        name = (am.get("name") or "").lower()
        if classification == "code":
            if "coder" in name or "qwen" in name:
                return node
        elif classification == "general":
            if "mistral" in name or "llama" in name:
                return node
        else:
            return None
    return None


async def get_inference_url(query: str) -> str:
    if not query:
        return LLAMA_SERVER_BASE
    classification = await classify_query(query)
    if classification in ("search", "rag"):
        return LLAMA_SERVER_BASE
    node = select_node(classification)
    if node:
        am = node.get("active_model") or {}
        port = am.get("port", 8081)
        ip = node.get("ip") or "127.0.0.1"
        return f"http://{ip}:{port}/v1"
    return LLAMA_SERVER_BASE
