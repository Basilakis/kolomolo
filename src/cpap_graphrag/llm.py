"""
Thin LLM helper with token/cost accounting.

Centralizes Anthropic calls so every LLM use can be priced consistently. `cost_usd` is then
attached to serving results and aggregated by the eval harness (we proposed cost as a metric,
so we must actually measure it).

PRICES are per 1M tokens (USD). VERIFY against current Anthropic pricing before quoting numbers
in SOLUTION.md — they are configurable here so the metric stays honest if rates change.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import settings

# {model: (input_per_mtok, output_per_mtok)} — approximate; verify before publishing.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
_DEFAULT_PRICE = (3.0, 15.0)


@dataclass
class LLMResult:
    message: object              # the raw anthropic Message
    input_tokens: int
    output_tokens: int
    cost_usd: float

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.message.content if b.type == "text")


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, _DEFAULT_PRICE)
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 6)


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def call(model: str, **kwargs) -> LLMResult:
    """Make a messages.create call and return the result with priced usage."""
    msg = _client().messages.create(model=model, **kwargs)
    usage = getattr(msg, "usage", None)
    itok = getattr(usage, "input_tokens", 0) or 0
    otok = getattr(usage, "output_tokens", 0) or 0
    return LLMResult(message=msg, input_tokens=itok, output_tokens=otok,
                     cost_usd=cost_of(model, itok, otok))
