"""JarvisChat routers - Cluster status API."""
from fastapi import APIRouter

import cluster

router = APIRouter()


@router.get("/api/cluster")
async def cluster_status():
    return {
        "nodes": {name: _strip_internal(node) for name, node in cluster.CLUSTER_NODES.items()},
        "node_count": len(cluster.CLUSTER_NODES),
        "coordinator": cluster.CLUSTER_COORDINATOR,
        "events": list(cluster.CLUSTER_EVENTS),
    }


def _strip_internal(node: dict) -> dict:
    return {k: v for k, v in node.items() if k in {
        "name", "type", "status", "capabilities", "active_model", "load",
        "registered_at", "last_seen",
    }}
