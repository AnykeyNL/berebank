"""Response shaping shared by the REST layer and the MCP server.

Analysis responses carry the chart payload — the candles of the display window
and an overlay series per indicator — because the web app draws them. An agent
reading the same response over MCP pays for those arrays in context tokens and
almost never uses them, so tools expose a ``verbose`` flag that routes through
``compact_analysis``.
"""
from decimal import Decimal


def plain_decimal(value: Decimal | None) -> str | None:
    """Exact decimal string without trailing zeros or exponent notation.

    ``str(Decimal("0.0000000001"))`` yields ``"1E-10"``, which is correct but
    trips up clients that parse with a plain decimal reader — and tick sizes on
    meme coins reach exactly that magnitude.
    """
    if value is None:
        return None
    return format(value.normalize(), "f")


def compact_analysis(result: dict) -> dict:
    """Drop the chart payload, keeping every signal, value and explanation.

    Returns a copy: the callers hand in dicts they hold in a TTL cache and
    serve to the web app verbatim.
    """
    compact = {key: value for key, value in result.items() if key != "candles"}
    strategies = compact.get("strategies")
    if isinstance(strategies, dict):
        compact["strategies"] = {
            name: {**strategy, "series": {}}
            if isinstance(strategy, dict) and strategy.get("series")
            else strategy
            for name, strategy in strategies.items()
        }
    return compact


def shape_analysis(result: dict, verbose: bool) -> dict:
    return result if verbose else compact_analysis(result)
