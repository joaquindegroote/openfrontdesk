"""What a run's tokens cost. List prices in dollars per million tokens, as the providers
published them on November 21, 2026. A run on a model that isn't here shows no cost."""

from dataclasses import dataclass

# {model: {"input_tokens": ..., "output_tokens": ..., "cache_read_tokens": ..., ...}}
UsageByModel = dict[str, dict[str, int]]


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    # Input the provider read from its prompt cache, and input it wrote there.
    cache_read: float
    cache_write: float


PRICES = {
    # Claude writes to the cache at 1.25 times the input price and reads at a fraction of it.
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.10, cache_write=2.50),
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
    # OpenAI doesn't charge for writing.
    "gpt-5-mini": Price(input=0.25, output=2.00, cache_read=0.025, cache_write=0.25),
    "gpt-5-nano": Price(input=0.05, output=0.40, cache_read=0.005, cache_write=0.05),
}


def price_of(model: str) -> Price | None:
    """Providers answer with dated names ("gpt-5-mini-2025-08-07"): the longest name that
    starts it wins."""
    names = [name for name in PRICES if model.startswith(name)]
    return PRICES[max(names, key=len)] if names else None


def cost(usage: UsageByModel) -> float | None:
    """Dollars for what a conversation used. None when one of its models has no price, or
    when nothing was recorded."""
    if not usage:
        return None
    total = 0.0
    for model, tokens in usage.items():
        price = price_of(model)
        if price is None:
            return None
        read = tokens.get("cache_read_tokens", 0)
        written = tokens.get("cache_write_tokens", 0)
        total += (
            (tokens.get("input_tokens", 0) - read - written) * price.input
            + read * price.cache_read
            + written * price.cache_write
            + tokens.get("output_tokens", 0) * price.output
        )
    return total / 1_000_000


def add(total: UsageByModel, usage: UsageByModel) -> UsageByModel:
    for model, tokens in usage.items():
        kept = total.setdefault(model, {})
        for name, count in tokens.items():
            kept[name] = kept.get(name, 0) + count
    return total
