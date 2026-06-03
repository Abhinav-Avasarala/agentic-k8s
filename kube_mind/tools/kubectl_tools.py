from __future__ import annotations

from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

_core_v1: client.CoreV1Api | None = None
_apps_v1: client.AppsV1Api | None = None


def _apis() -> tuple[client.CoreV1Api, client.AppsV1Api]:
    global _core_v1, _apps_v1
    if _core_v1 is None:
        config.load_kube_config()
        _core_v1 = client.CoreV1Api()
        _apps_v1 = client.AppsV1Api()
    return _core_v1, _apps_v1


def _deployment_name(params: dict[str, Any]) -> str:
    return params.get("deployment") or params["name"]


def _node_name(params: dict[str, Any]) -> str:
    return params.get("node") or params["name"]


def scale_deployment(params: dict[str, Any]) -> None:
    _, apps = _apis()
    apps.patch_namespaced_deployment_scale(
        name=_deployment_name(params),
        namespace=params.get("namespace", "default"),
        body={"spec": {"replicas": params["replicas"]}},
    )


def cordon_node(params: dict[str, Any]) -> None:
    core, _ = _apis()
    core.patch_node(
        name=_node_name(params),
        body={"spec": {"unschedulable": True}},
    )


def drain_node(params: dict[str, Any]) -> None:
    core, _ = _apis()
    node_name = _node_name(params)

    core.patch_node(node_name, {"spec": {"unschedulable": True}})

    pods = core.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}")
    for pod in pods.items:
        if any(ref.kind == "DaemonSet" for ref in (pod.metadata.owner_references or [])):
            continue
        try:
            core.create_namespaced_pod_eviction(
                name=pod.metadata.name,
                namespace=pod.metadata.namespace,
                body=client.V1Eviction(
                    metadata=client.V1ObjectMeta(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                    )
                ),
            )
        except ApiException as e:
            if e.status != 404:
                raise


def patch_resources(params: dict[str, Any]) -> None:
    _, apps = _apis()
    deployment = _deployment_name(params)
    namespace = params.get("namespace", "default")

    dep = apps.read_namespaced_deployment(name=deployment, namespace=namespace)
    containers = [
        {"name": c.name, "resources": params["resources"]}
        for c in dep.spec.template.spec.containers
    ]
    apps.patch_namespaced_deployment(
        name=deployment,
        namespace=namespace,
        body={"spec": {"template": {"spec": {"containers": containers}}}},
    )


def taint_node(params: dict[str, Any]) -> None:
    core, _ = _apis()
    node_name = _node_name(params)
    node = core.read_node(node_name)
    taints = [
        {"key": t.key, "value": t.value, "effect": t.effect}
        for t in (node.spec.taints or [])
    ]
    taints.append({"key": params["key"], "value": params["value"], "effect": params["effect"]})
    core.patch_node(node_name, {"spec": {"taints": taints}})
