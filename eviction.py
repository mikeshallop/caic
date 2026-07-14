"""
cAIc — Score-based RAG vector eviction with hysteresis.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import httpx

from config import (
    QDRANT_URL, RAG_COLLECTION,
    RAG_MAX_VECTORS, RAG_EVICTION_HIGH_WATER, RAG_EVICTION_LOW_WATER,
    RAG_EVICTION_BATCH, RAG_PINNED_SOURCES, RAG_GRACE_HOURS,
    RAG_ACCESS_WEIGHT, RAG_AGE_WEIGHT,
)

log = logging.getLogger("caic")

eviction_lock = asyncio.Lock()
EVICTION_LOG: list[dict] = []


async def _update_retrieval_count(point_id: str, current_count: int = 0):
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "retrieval_count": current_count + 1,
                "last_accessed": datetime.now(timezone.utc).isoformat(),
            }
            resp = await client.put(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/payload",
                json={"points": [point_id], "payload": payload},
                timeout=5.0,
            )
            if resp.status_code not in (200, 201):
                log.warning(f"Failed to increment retrieval count for {point_id}: {resp.status_code}")
    except Exception as e:
        log.warning(f"Error incrementing retrieval count for {point_id}: {e}")


async def get_collection_count() -> int:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}",
                timeout=10.0,
            )
            if resp.status_code == 200:
                info = resp.json().get("result", {})
                return info.get("points_count", info.get("vectors_count", 0))
    except Exception as e:
        log.warning(f"get_collection_count error: {e}")
    return 0


async def get_collection_stats() -> dict:
    count = await get_collection_count()
    high_water_pct = int(RAG_EVICTION_HIGH_WATER * 100)
    low_water_pct = int(RAG_EVICTION_LOW_WATER * 100)
    percent_full = round((count / RAG_MAX_VECTORS) * 100, 1) if RAG_MAX_VECTORS > 0 else 0
    return {
        "vector_count": count,
        "max_vectors": RAG_MAX_VECTORS,
        "high_water_mark": int(RAG_MAX_VECTORS * RAG_EVICTION_HIGH_WATER),
        "low_water_mark": int(RAG_MAX_VECTORS * RAG_EVICTION_LOW_WATER),
        "high_water_pct": high_water_pct,
        "low_water_pct": low_water_pct,
        "percent_full": percent_full,
        "pinned_sources": list(RAG_PINNED_SOURCES),
    }


async def evict_batch(batch_size: int) -> int:
    filter_conditions = {
        "must_not": [
            {"match": {"key": "source", "value": src}}
            for src in RAG_PINNED_SOURCES
        ]
    }
    try:
        async with httpx.AsyncClient() as client:
            scroll_resp = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/scroll",
                json={
                    "filter": filter_conditions,
                    "limit": min(batch_size * 10, 10000),
                    "with_payload": True,
                    "with_vector": False,
                },
                timeout=30.0,
            )
            if scroll_resp.status_code != 200:
                log.warning(f"Eviction scroll failed: {scroll_resp.status_code}")
                return 0

            points = scroll_resp.json().get("result", {}).get("points", [])
            if not points:
                return 0

            now = datetime.now(timezone.utc)
            scored = []
            for p in points:
                payload = p.get("payload", {})
                date_str = payload.get("ingest_date") or payload.get("upload_date", "")
                if date_str:
                    age_hours = (now - datetime.fromisoformat(date_str)).total_seconds() / 3600
                else:
                    age_hours = 999999

                if age_hours < RAG_GRACE_HOURS:
                    continue

                retrieval_count = payload.get("retrieval_count", 0) or 0
                score = retrieval_count * RAG_ACCESS_WEIGHT + age_hours * RAG_AGE_WEIGHT
                last_accessed = payload.get("last_accessed", date_str)
                scored.append((score, last_accessed, p["id"]))

            if not scored:
                log.warning("No evictable vectors found (all pinned or newborn)")
                return 0

            scored.sort(key=lambda x: (x[0], x[1]))
            to_delete = [p[2] for p in scored[:batch_size]]
            if not to_delete:
                return 0

            delete_resp = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/delete",
                json={"points": to_delete},
                timeout=30.0,
            )
            if delete_resp.status_code not in (200, 201):
                log.warning(f"Eviction delete failed: {delete_resp.status_code}")
                return 0

            return len(to_delete)
    except Exception as e:
        log.warning(f"evict_batch error: {e}")
        return 0


async def maybe_evict() -> int:
    if RAG_MAX_VECTORS <= 0:
        return 0
    effective_batch = max(RAG_EVICTION_BATCH, 1)

    async with eviction_lock:
        count = await get_collection_count()
        threshold_high = int(RAG_MAX_VECTORS * RAG_EVICTION_HIGH_WATER)
        threshold_low = int(RAG_MAX_VECTORS * RAG_EVICTION_LOW_WATER)

        if count < threshold_high:
            return 0

        total_evicted = 0
        while count >= threshold_low:
            if total_evicted > 0 and count < threshold_low:
                break
            deleted = await evict_batch(effective_batch)
            if deleted == 0:
                break
            total_evicted += deleted
            count -= deleted
            if count < threshold_high and total_evicted > 0:
                break
            if count < threshold_low:
                break

        if total_evicted > 0:
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "count": total_evicted,
                "remaining": count,
            }
            EVICTION_LOG.append(entry)
            if len(EVICTION_LOG) > 1000:
                EVICTION_LOG.pop(0)
            log.info(f"Evicted {total_evicted} vectors ({count} remaining)")

        return total_evicted


async def get_rag_operational_stats() -> dict:
    stats = await get_collection_stats()
    now = datetime.now(timezone.utc)
    cutoff_1m = now - timedelta(minutes=1)
    cutoff_5m = now - timedelta(minutes=5)
    cutoff_30m = now - timedelta(minutes=30)

    eviction_1m = sum(
        e["count"] for e in EVICTION_LOG
        if datetime.fromisoformat(e["timestamp"]) > cutoff_1m
    )
    eviction_5m = sum(
        e["count"] for e in EVICTION_LOG
        if datetime.fromisoformat(e["timestamp"]) > cutoff_5m
    )
    eviction_30m = sum(
        e["count"] for e in EVICTION_LOG
        if datetime.fromisoformat(e["timestamp"]) > cutoff_30m
    )

    pinned_count = 0
    avg_retrieval_count = 0.0
    at_risk_count = 0

    try:
        async with httpx.AsyncClient() as client:
            pinned_filter = {
                "should": [
                    {"match": {"key": "source", "value": src}}
                    for src in RAG_PINNED_SOURCES
                ]
            }
            pinned_resp = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/scroll",
                json={"filter": pinned_filter, "limit": 10000, "with_payload": True, "with_vector": False},
                timeout=10.0,
            )
            if pinned_resp.status_code == 200:
                pinned_count = len(pinned_resp.json().get("result", {}).get("points", []))

            nonpinned_filter = {
                "must_not": [
                    {"match": {"key": "source", "value": src}}
                    for src in RAG_PINNED_SOURCES
                ]
            }
            np_resp = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/scroll",
                json={"filter": nonpinned_filter, "limit": 10000, "with_payload": True, "with_vector": False},
                timeout=10.0,
            )
            if np_resp.status_code == 200:
                points = np_resp.json().get("result", {}).get("points", [])
                if points:
                    retrievals = []
                    scored = []
                    for p in points:
                        payload = p.get("payload", {})
                        rc = payload.get("retrieval_count", 0) or 0
                        retrievals.append(rc)
                        date_str = payload.get("ingest_date") or payload.get("upload_date", "")
                        if date_str:
                            age_hours = (now - datetime.fromisoformat(date_str)).total_seconds() / 3600
                        else:
                            age_hours = 999999
                        score = rc * RAG_ACCESS_WEIGHT + age_hours * RAG_AGE_WEIGHT
                        last_accessed = payload.get("last_accessed", date_str)
                        scored.append((score, last_accessed))

                    avg_retrieval_count = round(sum(retrievals) / len(retrievals), 2)

                    scored.sort(key=lambda x: (x[0], x[1]))
                    at_risk_threshold = max(1, len(scored) // 10)
                    at_risk_count = at_risk_threshold
    except Exception as e:
        log.warning(f"RAG operational stats scroll error: {e}")

    stats.update({
        "grace_hours": RAG_GRACE_HOURS,
        "eviction_counts_last_1m": eviction_1m,
        "eviction_counts_last_5m": eviction_5m,
        "eviction_counts_last_30m": eviction_30m,
        "pinned_count": pinned_count,
        "avg_retrieval_count": avg_retrieval_count,
        "at_risk_count": at_risk_count,
    })
    return stats
