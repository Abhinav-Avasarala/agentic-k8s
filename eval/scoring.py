from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Which params must match (in addition to action) for two ops to be considered equivalent.
# All keys here reference the NORMALIZED param dict (see _normalize_params).
_KEY_PARAMS: dict[str, list[str]] = {
    "create_node_pool":       ["name"],
    "delete_node_pool":       ["name"],
    "resize_node_pool":       ["name", "count"],   # "size" is normalized to "count"
    "scale_deployment":       ["name", "replicas"], # "deployment" is normalized to "name"
    "cordon_node":            ["name"],             # "node" is normalized to "name"
    "drain_node":             ["name"],
    # taint: don't require node name — it's non-deterministic before the node exists
    "taint_node":             ["key", "effect"],
    "untaint_node":           ["key"],
    "uncordon_node":          ["name"],
    "patch_resources":        ["name"],
    "check_nodes_ready":      ["pool"],
    "check_deployment_ready": ["name"],             # "deployment" is normalized to "name"
    "check_pod_scheduled":    ["name"],
    # prometheus_query: just presence — PromQL strings vary too much to exact-match
    "prometheus_query":       [],
}


def _normalize_params(op: dict[str, Any]) -> dict[str, Any]:
    """Unify param name/value variants so scoring is not fooled by alias differences."""
    p = dict(op.get("params", {}))
    # key name aliases
    if "deployment" in p and "name" not in p:
        p["name"] = p.pop("deployment")
    if "node" in p and "name" not in p:
        p["name"] = p.pop("node")
    # GKE API uses "size" and "count" interchangeably for node pool node count
    if "size" in p and "count" not in p:
        p["count"] = p.pop("size")
    # Normalize whole-number floats to int so "5.0" == "5" in str comparison
    for k, v in p.items():
        if isinstance(v, float) and v == int(v):
            p[k] = int(v)
    return p


# Actions that are interchangeable as post-scale deployment verify steps.
_ACTION_SYNONYMS: dict[str, str] = {
    "check_pod_scheduled":    "check_deployment_ready",
    "check_deployment_ready": "check_pod_scheduled",
}


def _ops_match(gen: dict[str, Any], exp: dict[str, Any]) -> bool:
    gen_action = gen.get("action")
    exp_action = exp.get("action")

    # Exact action match OR known synonym pair
    if gen_action != exp_action and _ACTION_SYNONYMS.get(gen_action) != exp_action:
        return False

    gen_p = _normalize_params(gen)
    exp_p = _normalize_params(exp)

    for key in _KEY_PARAMS.get(exp_action, []):
        exp_val = exp_p.get(key)
        # None in expected = wildcard: any generated value (or absent key) is acceptable
        if exp_val is None:
            continue
        if str(gen_p.get(key, "")).lower() != str(exp_val).lower():
            return False

    return True


@dataclass
class CaseResult:
    case_id: str
    description: str
    tags: list[str]
    generated_ops: list[dict]
    expected_ops: list[dict]
    precision: float
    recall: float
    f1: float
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    error: str = ""
    # Indices populated by score_case — used by missed_ops/extra_ops so they
    # are always consistent with the precision/recall calculation.
    _matched_gen: set = field(default_factory=set)
    _matched_exp: set = field(default_factory=set)

    @property
    def passed(self) -> bool:
        return self.f1 >= 0.8 and not self.error

    @property
    def missed_ops(self) -> list[dict]:
        """Expected ops that were not matched by any generated op."""
        return [op for j, op in enumerate(self.expected_ops) if j not in self._matched_exp]

    @property
    def extra_ops(self) -> list[dict]:
        """Generated ops that did not match any expected op."""
        return [op for i, op in enumerate(self.generated_ops) if i not in self._matched_gen]


def score_case(
    case_id: str,
    description: str,
    tags: list[str],
    generated: list[dict],
    expected: list[dict],
    usage: dict[str, Any],
    error: str = "",
) -> CaseResult:
    base = dict(
        case_id=case_id,
        description=description,
        tags=tags,
        generated_ops=generated,
        expected_ops=expected,
        latency_ms=usage.get("latency_ms", 0),
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        error=error,
    )

    if error:
        return CaseResult(**base, precision=0.0, recall=0.0, f1=0.0)

    # Both empty → perfect score (correct no-op)
    if not expected and not generated:
        return CaseResult(**base, precision=1.0, recall=1.0, f1=1.0, _matched_gen=set(), _matched_exp=set())

    # Greedy match: pair each generated op to the first unmatched expected op it equals.
    # Store matched indices so missed_ops/extra_ops are consistent with these scores.
    matched_exp: set[int] = set()
    matched_gen: set[int] = set()
    for i, gen_op in enumerate(generated):
        for j, exp_op in enumerate(expected):
            if j not in matched_exp and _ops_match(gen_op, exp_op):
                matched_exp.add(j)
                matched_gen.add(i)
                break

    tp = len(matched_exp)
    precision = tp / len(generated) if generated else 0.0
    recall = tp / len(expected) if expected else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return CaseResult(
        **base,
        precision=precision, recall=recall, f1=f1,
        _matched_gen=matched_gen, _matched_exp=matched_exp,
    )
