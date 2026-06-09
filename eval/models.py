from __future__ import annotations

import os
import time
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ModelAdapter(Protocol):
    name: str

    def complete(self, system: str, user: str) -> tuple[str, dict[str, Any]]:
        """Call the model. Returns (response_text, usage_stats)."""
        ...


class OpenAIAdapter:
    def __init__(self, model: str = "gpt-4o"):
        self.model = model
        self.name = model

    def complete(self, system: str, user: str) -> tuple[str, dict[str, Any]]:
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        t0 = time.monotonic()
        resp = client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content, {
            "latency_ms": round((time.monotonic() - t0) * 1000),
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
        }


class AnthropicAdapter:
    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model
        self.name = model

    def complete(self, system: str, user: str) -> tuple[str, dict[str, Any]]:
        try:
            import anthropic
        except ImportError:
            raise ImportError("pip install anthropic to use Anthropic models")

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        t0 = time.monotonic()
        resp = client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text, {
            "latency_ms": round((time.monotonic() - t0) * 1000),
            "prompt_tokens": resp.usage.input_tokens,
            "completion_tokens": resp.usage.output_tokens,
        }


# Cost per 1K tokens: (input, output)
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o":                    (0.0025, 0.01),
    "gpt-4o-mini":               (0.00015, 0.0006),
    "claude-opus-4-8":           (0.015,  0.075),
    "claude-sonnet-4-6":         (0.003,  0.015),
    "claude-haiku-4-5-20251001": (0.00025, 0.00125),
}


def estimate_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    inp, out = _PRICING.get(model_name, (0.003, 0.015))
    return (prompt_tokens * inp + completion_tokens * out) / 1000


def get_adapter(model_str: str) -> ModelAdapter:
    """Parse a model string into the right adapter.

    Examples: 'gpt-4o', 'gpt-4o-mini', 'claude-sonnet-4-6', 'claude-opus-4-8'
    """
    if model_str.startswith("claude"):
        return AnthropicAdapter(model_str)
    elif model_str.startswith(("gpt", "o1", "o3")):
        return OpenAIAdapter(model_str)
    else:
        raise ValueError(
            f"Unknown model '{model_str}'. "
            "Use 'gpt-4o', 'gpt-4o-mini', 'claude-sonnet-4-6', 'claude-opus-4-8', etc."
        )
