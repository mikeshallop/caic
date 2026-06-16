"""
JarvisChat - SearXNG integration, perplexity scoring, refusal/hedge detection.
"""
import logging
import math
import re
from urllib.parse import urlparse

import httpx

from config import SEARXNG_BASE, PERPLEXITY_THRESHOLD, REFUSAL_PATTERNS, HEDGE_PATTERNS

log = logging.getLogger("jarvischat")


def sanitize_outbound_url(url: str) -> str:
    if not url:
        return ""
    candidate = url.strip()
    parsed = urlparse(candidate)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return candidate
    return ""


def calculate_perplexity(logprobs: list) -> float:
    if not logprobs:
        return 0.0
    avg_logprob = sum(lp["logprob"] for lp in logprobs) / len(logprobs)
    return math.exp(-avg_logprob)


def is_uncertain(logprobs: list, threshold: float = PERPLEXITY_THRESHOLD) -> bool:
    if not logprobs:
        return False
    perplexity = calculate_perplexity(logprobs)
    log.info(f"Perplexity: {perplexity:.2f} (threshold: {threshold})")
    return perplexity > threshold


def is_refusal(text: str) -> bool:
    match = REFUSAL_PATTERNS.search(text)
    if match:
        log.info(f"Refusal detected: '{match.group()}'")
        return True
    return False


def clean_hedging(text: str) -> str:
    cleaned = text
    for pattern in HEDGE_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def format_search_results(results: list) -> str:
    if not results:
        return ""
    lines = ["[LIVE WEB DATA]\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        if r["content"]:
            lines.append(f"   {r['content']}")
        lines.append("")
    lines.append("\nAnswer directly using the data above. No apologies. No disclaimers. Just answer.")
    return "\n".join(lines)


def format_direct_answer(question: str, results: list) -> str:
    if not results:
        return "No search results found."
    lines = ["Here's what I found:\n"]
    for r in results[:3]:
        lines.append(f"**{r['title']}**")
        if r["content"]:
            lines.append(f"{r['content']}")
        lines.append("")
    return "\n".join(lines).strip()


def extract_search_query(user_message: str) -> str:
    query = user_message.strip()
    if re.search(r"temperature|weather", query, re.IGNORECASE):
        query = re.sub(r"^what('?s| is) the ", "", query, flags=re.IGNORECASE) + " right now degrees"
    if re.search(r"price|spot price", query, re.IGNORECASE):
        query = re.sub(r"^(what('?s| is)|can you tell me) the ", "", query, flags=re.IGNORECASE) + " today USD"
    query = re.sub(
        r"^(what|who|where|when|why|how|is|are|can|could|would|should|do|does|did)\s+",
        "", query, flags=re.IGNORECASE,
    )
    query = re.sub(r"[?!.]+$", "", query)
    return query[:100].strip() or user_message[:100]


async def query_searxng(query: str, max_results: int = 5) -> list:
    log.info(f"Querying SearXNG: '{query}'")
    async with httpx.AsyncClient() as client:
        weather_match = re.search(
            r"(?:weather|temperature|forecast)\s+(?:in\s+)?(.+?)(?:\s+right now|\s+today|\s+degrees)?$",
            query, re.IGNORECASE,
        )
        if weather_match or "weather" in query.lower() or "temperature" in query.lower():
            location = (
                weather_match.group(1) if weather_match
                else re.sub(r"(weather|temperature|forecast|right now|today|degrees)", "", query, flags=re.IGNORECASE).strip()
            )
            if location:
                try:
                    resp = await client.get(f"https://wttr.in/{location}?format=3", timeout=10.0,
                                            headers={"User-Agent": "curl/7.68.0"})
                    if resp.status_code == 200:
                        return [{"title": "Current Weather",
                                 "url": sanitize_outbound_url(f"https://wttr.in/{location}"),
                                 "content": resp.text.strip()}]
                except Exception as e:
                    log.warning(f"wttr.in error: {e}")

        try:
            resp = await client.get(
                f"{SEARXNG_BASE}/search",
                params={"q": query, "format": "json", "categories": "general"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for answer in data.get("answers", []):
                    results.append({"title": "Direct Answer", "url": "", "content": answer})
                for box in data.get("infoboxes", []):
                    content = box.get("content", "")
                    if not content and box.get("attributes"):
                        content = " | ".join([f"{a.get('label','')}: {a.get('value','')}" for a in box["attributes"]])
                    results.append({
                        "title": box.get("infobox", "Info"),
                        "url": sanitize_outbound_url(box.get("urls", [{}])[0].get("url", "") if box.get("urls") else ""),
                        "content": content,
                    })
                for r in data.get("results", [])[:max_results]:
                    results.append({"title": r.get("title", ""), "url": sanitize_outbound_url(r.get("url", "")), "content": r.get("content", "")})
                log.info(f"SearXNG returned {len(results)} results")
                return results
        except Exception as e:
            log.error(f"SearXNG error: {e}")
    return []
