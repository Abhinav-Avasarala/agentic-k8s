from __future__ import annotations

import time
from typing import Any

from google.cloud import container_v1
from google.api_core.exceptions import NotFound, PermissionDenied, GoogleAPIError


def _client() -> container_v1.ClusterManagerClient:
    return container_v1.ClusterManagerClient()


def _cluster_path(project: str, zone: str, cluster: str) -> str:
    return f"projects/{project}/locations/{zone}/clusters/{cluster}"


def _pool_path(project: str, zone: str, cluster: str, pool: str) -> str:
    return f"{_cluster_path(project, zone, cluster)}/nodePools/{pool}"


def _get_pool(client: container_v1.ClusterManagerClient, project: str, zone: str, cluster: str, pool_name: str):
    """Return the named pool proto, or None if it doesn't exist."""
    resp = client.list_node_pools(parent=_cluster_path(project, zone, cluster))
    return next((p for p in resp.node_pools if p.name == pool_name), None)


def _wait_running(client, project, zone, cluster, pool_name, interval=10):
    """Block until the pool exists and is RUNNING."""
    while True:
        pool = _get_pool(client, project, zone, cluster, pool_name)
        if pool and pool.status == container_v1.NodePool.Status.RUNNING:
            return
        if pool and pool.status == container_v1.NodePool.Status.ERROR:
            raise RuntimeError(f"Node pool {pool_name} entered ERROR state")
        time.sleep(interval)


def _wait_gone(client, project, zone, cluster, pool_name, interval=10):
    """Block until the pool no longer exists."""
    while True:
        if _get_pool(client, project, zone, cluster, pool_name) is None:
            return
        time.sleep(interval)


def get_live_cluster_status(project: str, zone: str, cluster: str) -> dict[str, Any] | None:
    """Fetch live cluster state from the GKE API.

    Returns a cluster dict compatible with state.json, or None if the cluster
    does not exist (was deleted externally). Raises RuntimeError on auth/API errors.
    """
    client = _client()
    path = _cluster_path(project, zone, cluster)
    try:
        c = client.get_cluster(name=path)
        pools_resp = client.list_node_pools(parent=path)
    except NotFound:
        return None
    except (PermissionDenied, GoogleAPIError) as e:
        raise RuntimeError(f"GKE API error: {e}") from e

    node_pools = []
    for p in pools_resp.node_pools:
        pool_entry: dict[str, Any] = {
            "name": p.name,
            "machine": p.config.machine_type,
            "count": p.initial_node_count,
            "status": container_v1.NodePool.Status(p.status).name,
        }
        if p.config.accelerators:
            pool_entry["gpu"] = p.config.accelerators[0].accelerator_type
        node_pools.append(pool_entry)

    return {
        "name": c.name,
        "zone": zone,
        "project": project,
        "status": container_v1.Cluster.Status(c.status).name,
        "node_pools": node_pools,
    }


def create_node_pool(project: str, zone: str, cluster: str, params: dict[str, Any]) -> None:
    client = _client()

    accelerators = []
    if params.get("gpu"):
        accelerators.append(container_v1.AcceleratorConfig(
            accelerator_count=1,
            accelerator_type="nvidia-tesla-t4",
        ))

    pool = container_v1.NodePool(
        name=params["name"],
        config=container_v1.NodeConfig(
            machine_type=params.get("machine", "e2-medium"),
            disk_size_gb=50,
            accelerators=accelerators,
            preemptible=bool(params.get("preemptible", False)),
        ),
        initial_node_count=params.get("count", 1),
    )

    client.create_node_pool(request=container_v1.CreateNodePoolRequest(
        parent=_cluster_path(project, zone, cluster),
        node_pool=pool,
    ))
    _wait_running(client, project, zone, cluster, params["name"])


def delete_node_pool(project: str, zone: str, cluster: str, params: dict[str, Any]) -> None:
    client = _client()
    client.delete_node_pool(request=container_v1.DeleteNodePoolRequest(
        name=_pool_path(project, zone, cluster, params["name"]),
    ))
    _wait_gone(client, project, zone, cluster, params["name"])


def resize_node_pool(project: str, zone: str, cluster: str, params: dict[str, Any]) -> None:
    client = _client()
    client.set_node_pool_size(request=container_v1.SetNodePoolSizeRequest(
        name=_pool_path(project, zone, cluster, params["name"]),
        node_count=params["count"],
    ))
    _wait_running(client, project, zone, cluster, params["name"])
