from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

_core_v1: client.CoreV1Api | None = None
_apps_v1: client.AppsV1Api | None = None


class VerifyError(Exception):
    pass


def _apis() -> tuple[client.CoreV1Api, client.AppsV1Api]:
    global _core_v1, _apps_v1
    if _core_v1 is None:
        config.load_kube_config()
        _core_v1 = client.CoreV1Api()
        _apps_v1 = client.AppsV1Api()
    return _core_v1, _apps_v1


def _prometheus_url() -> str:
    return os.environ.get("PROMETHEUS_URL", "http://localhost:9090")


def check_nodes_ready(params: dict[str, Any]) -> dict[str, Any]:
    core, _ = _apis()
    pool = params.get("pool", "")

    nodes = core.list_node(label_selector=f"cloud.google.com/gke-nodepool={pool}")
    if not nodes.items:
        raise VerifyError(f"No nodes found for pool '{pool}'")

    ready, not_ready = [], []
    for node in nodes.items:
        is_ready = any(
            c.type == "Ready" and c.status == "True"
            for c in (node.status.conditions or [])
        )
        (ready if is_ready else not_ready).append(node.metadata.name)

    if not_ready:
        raise VerifyError(
            f"Pool '{pool}': {len(not_ready)}/{len(nodes.items)} node(s) not ready: "
            + ", ".join(not_ready)
        )

    return {"pool": pool, "ready": len(ready), "total": len(nodes.items)}


def check_pod_scheduled(params: dict[str, Any]) -> dict[str, Any]:
    core, apps = _apis()
    deployment = params.get("deployment") or params.get("name", "")
    namespace = params.get("namespace", "default")

    try:
        dep = apps.read_namespaced_deployment(name=deployment, namespace=namespace)
    except ApiException as e:
        raise VerifyError(f"Deployment '{deployment}' not found: {e.reason}")

    desired = dep.spec.replicas or 1
    labels = dep.spec.selector.match_labels or {}
    label_selector = ",".join(f"{k}={v}" for k, v in labels.items())

    pods = core.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
    scheduled = sum(1 for p in pods.items if p.spec.node_name is not None)

    if scheduled < desired:
        raise VerifyError(
            f"Deployment '{deployment}': only {scheduled}/{desired} pod(s) scheduled"
        )

    return {"deployment": deployment, "scheduled": scheduled, "desired": desired}


def check_deployment_ready(params: dict[str, Any]) -> dict[str, Any]:
    _, apps = _apis()
    deployment = params.get("deployment") or params.get("name", "")
    namespace = params.get("namespace", "default")

    try:
        dep = apps.read_namespaced_deployment(name=deployment, namespace=namespace)
    except ApiException as e:
        raise VerifyError(f"Deployment '{deployment}' not found: {e.reason}")

    desired = dep.spec.replicas or 1
    ready = dep.status.ready_replicas or 0

    if ready < desired:
        raise VerifyError(
            f"Deployment '{deployment}': only {ready}/{desired} replicas ready"
        )

    return {"deployment": deployment, "ready": ready, "desired": desired}


def run_prometheus_query(params: dict[str, Any]) -> dict[str, Any]:
    query = params.get("query", "")
    if not query:
        raise VerifyError("prometheus_query op is missing a 'query' param")

    # Auto-start port-forward if Prometheus was installed via kube-mind init
    try:
        from kube_mind.tools.monitoring_tools import ensure_port_forward
        ensure_port_forward()
    except Exception:
        pass  # not installed via kube-mind — fall through to URL check below

    url = _prometheus_url()
    req_url = f"{url}/api/v1/query?" + urllib.parse.urlencode({"query": query})

    try:
        with urllib.request.urlopen(req_url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        raise VerifyError(
            f"Prometheus unreachable at {url}: {e}\n"
            "  Set PROMETHEUS_URL in .env or port-forward: "
            "kubectl port-forward svc/prometheus-kube-prometheus-prometheus 9090:9090 -n monitoring"
        )

    if data.get("status") != "success":
        raise VerifyError(f"Prometheus query error: {data.get('error', 'unknown')}")

    return {
        "label": params.get("label", ""),
        "unit": params.get("unit", "%"),
        "query": query,
        "warn_above": params.get("warn_above"),
        "crit_above": params.get("crit_above"),
        "results": data["data"]["result"],
    }
