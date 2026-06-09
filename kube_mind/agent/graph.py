from __future__ import annotations

import json
import os
import re
from typing import TypedDict, Any

from openai import OpenAI
from langgraph.graph import StateGraph, START, END

from kube_mind.agent.prompts import SYSTEM_PROMPT


class AgentState(TypedDict):
    intent: str
    cluster_state: dict[str, Any]
    ops: list[dict[str, Any]]
    dry_run: bool
    verify_results: list  # list of (op, result_dict) pairs collected during execution


def _parse_ops(text: str) -> list[dict[str, Any]]:
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    raw = match.group(1).strip() if match else text.strip()
    return json.loads(raw)


def planner_node(state: AgentState) -> AgentState:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    user_message = (
        f"Current cluster state:\n{json.dumps(state['cluster_state'], indent=2)}\n\n"
        f"User intent: {state['intent']}"
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2048,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    return {**state, "ops": _parse_ops(response.choices[0].message.content)}


def _capture_before(op: dict[str, Any], cluster: dict[str, Any]) -> dict[str, Any]:
    """Enrich a mutable op with its current state so it can be inverted by undo."""
    action = op["action"]
    params = op["params"]

    if action == "resize_node_pool":
        pool = next((p for p in cluster.get("node_pools", []) if p["name"] == params.get("name")), None)
        if pool:
            return {**op, "before": {"count": pool["count"]}}

    elif action == "delete_node_pool":
        pool = next((p for p in cluster.get("node_pools", []) if p["name"] == params.get("name")), None)
        if pool:
            return {**op, "before": pool}

    elif action == "scale_deployment":
        try:
            from kube_mind.tools.kubectl_tools import _apis
            _, apps = _apis()
            dep = apps.read_namespaced_deployment(
                name=params.get("deployment") or params["name"],
                namespace=params.get("namespace", "default"),
            )
            return {**op, "before": {"replicas": dep.spec.replicas or 1}}
        except Exception:
            pass

    return op


def executor_node(state: AgentState) -> AgentState:
    if state.get("dry_run"):
        return state

    from kube_mind.tools.gcloud_tools import create_node_pool, delete_node_pool, resize_node_pool
    from kube_mind.tools.kubectl_tools import (
        scale_deployment, cordon_node, drain_node, patch_resources,
        taint_node, uncordon_node, untaint_node,
    )

    _gcloud = {
        "create_node_pool": create_node_pool,
        "delete_node_pool": delete_node_pool,
        "resize_node_pool": resize_node_pool,
    }
    _kubectl = {
        "scale_deployment": scale_deployment,
        "cordon_node": cordon_node,
        "drain_node": drain_node,
        "patch_resources": patch_resources,
        "taint_node": taint_node,
        "uncordon_node": uncordon_node,
        "untaint_node": untaint_node,
    }

    cluster = state["cluster_state"]["cluster"]
    project, zone, name = cluster["project"], cluster["zone"], cluster["name"]

    from kube_mind.tools.prometheus_tools import (
        check_nodes_ready, check_pod_scheduled, check_deployment_ready,
        run_prometheus_query,
    )

    _verify = {
        "check_nodes_ready": check_nodes_ready,
        "check_pod_scheduled": check_pod_scheduled,
        "check_deployment_ready": check_deployment_ready,
        "prometheus_query": run_prometheus_query,
    }

    enriched_ops: list[dict[str, Any]] = []
    verify_results: list = list(state.get("verify_results", []))

    for op in state["ops"]:
        if op["type"] in ("gcloud", "kubectl"):
            op = _capture_before(op, cluster)
        enriched_ops.append(op)

        if op["type"] == "gcloud":
            fn = _gcloud.get(op["action"])
            if fn:
                fn(project, zone, name, op["params"])
        elif op["type"] == "kubectl":
            fn = _kubectl.get(op["action"])
            if fn:
                fn(op["params"])
        elif op["type"] == "verify":
            fn = _verify.get(op["action"])
            if fn:
                result = fn(op.get("params", {}))
                verify_results.append((op, result))

    return {**state, "ops": enriched_ops, "verify_results": verify_results}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("planner", planner_node)
    g.add_node("executor", executor_node)
    g.add_edge(START, "planner")
    g.add_edge("planner", "executor")
    g.add_edge("executor", END)
    return g.compile()


_graph = None


def plan(intent: str, cluster_state: dict[str, Any]) -> list[dict[str, Any]]:
    """Call the LLM and return the planned ops. Does not execute anything."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    result = _graph.invoke({
        "intent": intent,
        "cluster_state": cluster_state,
        "ops": [],
        "dry_run": True,  # skip executor node
    })
    return result["ops"]


def execute(
    ops: list[dict[str, Any]], cluster_state: dict[str, Any]
) -> tuple[list, list[dict[str, Any]]]:
    """Execute a pre-planned list of ops.

    Returns (verify_results, enriched_ops) where enriched_ops carry 'before'
    state snapshots that undo needs to invert each op.
    """
    result = executor_node({
        "ops": ops, "cluster_state": cluster_state,
        "dry_run": False, "intent": "", "verify_results": [],
    })
    return result.get("verify_results", []), result.get("ops", ops)
