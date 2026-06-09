from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from eval.models import ModelAdapter
from eval.scoring import CaseResult, score_case

_TEST_CASES_DEFAULT = Path(__file__).parent / "test_cases.json"


def _build_user_message(cluster_state: dict[str, Any], intent: str) -> str:
    return (
        f"Current cluster state:\n{json.dumps(cluster_state, indent=2)}\n\n"
        f"User intent: {intent}"
    )


def _parse_ops(text: str) -> list[dict[str, Any]]:
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    raw = match.group(1).strip() if match else text.strip()
    return json.loads(raw)


def load_cases(
    path: Path = _TEST_CASES_DEFAULT,
    filter_tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    with open(path) as f:
        cases = json.load(f)
    if filter_tags:
        cases = [c for c in cases if any(t in c.get("tags", []) for t in filter_tags)]
    return cases


def run_model(
    adapter: ModelAdapter,
    cases: list[dict[str, Any]],
    verbose: bool = False,
) -> list[CaseResult]:
    from kube_mind.agent.prompts import SYSTEM_PROMPT

    results: list[CaseResult] = []
    for case in cases:
        user_msg = _build_user_message(case["cluster_state"], case["intent"])
        try:
            text, usage = adapter.complete(SYSTEM_PROMPT, user_msg)
            generated = _parse_ops(text)
            error = ""
        except Exception as e:
            generated = []
            usage = {"latency_ms": 0, "prompt_tokens": 0, "completion_tokens": 0}
            error = str(e)

        result = score_case(
            case_id=case["id"],
            description=case["description"],
            tags=case.get("tags", []),
            generated=generated,
            expected=case["expected_ops"],
            usage=usage,
            error=error,
        )
        results.append(result)

        if verbose:
            status = "✓" if result.passed else "✗"
            print(f"  {status} {case['id']:30s}  F1={result.f1:.2f}")

    return results


def run_eval(
    adapters: list[ModelAdapter],
    cases: list[dict[str, Any]],
    verbose: bool = False,
) -> dict[str, list[CaseResult]]:
    """Run all adapters against all cases. Returns {model_name: [CaseResult]}."""
    return {
        adapter.name: run_model(adapter, cases, verbose=verbose)
        for adapter in adapters
    }
