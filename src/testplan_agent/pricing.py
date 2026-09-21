"""List prices, to estimate what a model call cost. An estimate, not a bill: prices change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

# Check against https://www.anthropic.com/pricing when updating.
PRICES_AS_OF = "2026-06-24"


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_read: Optional[float] = None  # when not a flat tenth of the input price

    @property
    def cache_write(self) -> float:
        return self.input * 1.25  # five-minute cache entries

    @property
    def cache_read_rate(self) -> float:
        return self.cache_read if self.cache_read is not None else self.input * 0.1


PRICES: Dict[str, Price] = {
    "claude-fable-5-1": Price(10.0, 50.0, cache_read=0.25),
    "claude-fable-5": Price(10.0, 50.0),
    "claude-opus-5": Price(5.0, 25.0),
    "claude-opus-4-8": Price(5.0, 25.0),
    "claude-opus-4-7": Price(5.0, 25.0),
    "claude-opus-4-6": Price(5.0, 25.0),
    "claude-sonnet-5": Price(2.0, 10.0),
    "claude-sonnet-4-6": Price(3.0, 15.0),
    "claude-haiku-4-5": Price(1.0, 5.0),
}


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Optional[float]:
    """Dollars for one call, or None for a model with no known price (unknown, not free)."""
    price = PRICES.get(model)
    if price is None:
        return None
    per_million = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read_rate
        + cache_write_tokens * price.cache_write
    )
    return per_million / 1_000_000
