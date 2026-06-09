#!/usr/bin/env python3
"""Quick eval runner — source .env before running."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from eval.models import get_adapter, estimate_cost
from eval.runner import load_cases, run_eval

adapter = get_adapter("gpt-4o")
cases = load_cases()
results = run_eval([adapter], cases, verbose=True)
model_results = results["gpt-4o"]

passed = sum(1 for r in model_results if r.passed)
avg_f1 = sum(r.f1 for r in model_results) / len(model_results)
total_p = sum(r.prompt_tokens for r in model_results)
total_c = sum(r.completion_tokens for r in model_results)
cost = estimate_cost("gpt-4o", total_p, total_c)
print(f"\n{passed}/{len(model_results)} passed  avg_F1={avg_f1:.2f}  est_cost=${cost:.3f}")

failures = [r for r in model_results if not r.passed]
for r in failures:
    if r.error:
        print(f"  ERROR {r.case_id}: {r.error[:100]}")
    else:
        missed = [f"{o['action']}({list(o.get('params', {}).values())})" for o in r.missed_ops]
        extra  = [f"{o['action']}({list(o.get('params', {}).values())})" for o in r.extra_ops]
        print(f"  x {r.case_id}  missed={missed}  extra={extra}")
