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


def executor_node(state: AgentState) -> AgentState:
    if state.get("dry_run"):
        return state

    from kube_mind.tools.gcloud_tools import create_node_pool, delete_node_pool, resize_node_pool
    from kube_mind.tools.kubectl_tools import scale_deployment, cordon_node, drain_node, patch_resources, taint_node

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
    }

    cluster = state["cluster_state"]["cluster"]
    project, zone, name = cluster["project"], cluster["zone"], cluster["name"]

    for op in state["ops"]:
        if op["type"] == "gcloud":
            fn = _gcloud.get(op["action"])
            if fn:
                fn(project, zone, name, op["params"])
        elif op["type"] == "kubectl":
            fn = _kubectl.get(op["action"])
            if fn:
                fn(op["params"])
        # verify ops handled in Step 9

    return state


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


def execute(ops: list[dict[str, Any]], cluster_state: dict[str, Any]) -> None:
    """Execute a pre-planned list of ops against the cluster."""
    executor_node({"ops": ops, "cluster_state": cluster_state, "dry_run": False, "intent": ""})
