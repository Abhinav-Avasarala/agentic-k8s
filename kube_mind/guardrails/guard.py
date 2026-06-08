from __future__ import annotations

import nest_asyncio
from enum import Enum
from pathlib import Path
from typing import Any

nest_asyncio.apply()

_CONFIG_DIR = Path(__file__).parent / "config"
_PROTECTED_POOLS = {"default-pool", "default"}


class RiskLevel(Enum):
    SAFE = "safe"
    WARN = "warn"                # yellow warning, plain y/n still works
    CONFIRM_BY_NAME = "confirm"  # must type the resource name to proceed


_RISKY_ACTIONS: dict[str, tuple[RiskLevel, str]] = {
    "delete_node_pool": (
        RiskLevel.CONFIRM_BY_NAME,
        "deletes a node pool and evicts all workloads running on it",
    ),
    "drain_node": (
        RiskLevel.CONFIRM_BY_NAME,
        "evicts all pods from the node — they will be rescheduled elsewhere",
    ),
    "cordon_node": (
        RiskLevel.WARN,
        "marks the node unschedulable — no new pods will be placed on it",
    ),
}

_rails = None


class GuardrailsError(Exception):
    pass


def _get_rails():
    global _rails
    if _rails is None:
        from nemoguardrails import RailsConfig, LLMRails
        config = RailsConfig.from_path(str(_CONFIG_DIR))
        _rails = LLMRails(config)
    return _rails


def check_intent(intent: str) -> None:
    """Run NeMo input rails against the user's intent.

    Uses the self_check_input library rail: makes one GPT-4o call with a
    Kubernetes-specific safety prompt. Raises GuardrailsError if blocked.
    """
    from nemoguardrails.rails.llm.options import GenerationOptions, GenerationLogOptions

    rails = _get_rails()
    opts = GenerationOptions(log=GenerationLogOptions(activated_rails=True))
    res = rails.generate(
        messages=[{"role": "user", "content": intent}],
        options=opts,
    )

    for rail in (res.log.activated_rails or []):
        if rail.stop:
            raise GuardrailsError(
                "Blocked by safety guardrails: the request is either dangerous "
                "or unrelated to Kubernetes infrastructure."
            )


def risk_check(ops: list[dict[str, Any]]) -> list[tuple[dict, RiskLevel, str]]:
    """Classify each op by risk level. Returns a list parallel to ops.

    Each entry is (op, RiskLevel, reason_string). Safe ops get RiskLevel.SAFE
    and an empty reason. Callers use this to decide the confirmation UX.
    """
    results = []
    for op in ops:
        action = op.get("action", "")
        if action in _RISKY_ACTIONS:
            level, reason = _RISKY_ACTIONS[action]
            results.append((op, level, reason))
        else:
            results.append((op, RiskLevel.SAFE, ""))
    return results


def check_ops(ops: list[dict[str, Any]]) -> None:
    """Pure-Python scan of planned ops for high-risk actions.

    Runs after planning, before execution — no LLM call needed.
    Raises GuardrailsError if an op would cause irreversible harm.
    """
    for op in ops:
        action = op.get("action", "")
        params = op.get("params", {})
        pool = params.get("name", "")

        if action == "delete_node_pool" and pool in _PROTECTED_POOLS:
            raise GuardrailsError(
                f"Refusing to delete '{pool}' — it is the cluster's primary node pool. "
                "Use gcloud directly if you're certain."
            )

        if action == "resize_node_pool" and int(params.get("count", 1)) == 0:
            raise GuardrailsError(
                f"Refusing to resize '{pool}' to 0 nodes — that would evict all running workloads."
            )
