"""Pure aggregator that collapses daily rates into monthly buckets.

Kept as its own module with no MCP / HTTP imports so it can be unit-tested
with trivial fixtures. The tool layer calls ``summarize_monthly(rates)`` and
attaches the result to the response.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from ..bccr.models import FlatRate


# --- Python idiom: `collections.defaultdict` --------------------------------
# A normal dict raises KeyError on missing keys. `defaultdict(list)` returns
# a fresh empty list instead, so you can write ``d[k].append(x)`` without
# checking ``if k not in d`` first. Handy when grouping a flat sequence by
# some key.


def _month_key(rate: FlatRate) -> str:
    """Return ``YYYY-MM`` from a ``FlatRate.observed_on``."""
    return f"{rate.observed_on.year:04d}-{rate.observed_on.month:02d}"


def _aggregate(values: list[float]) -> dict[str, float]:
    """Return min / max / mean of a non-empty list of floats, rounded to 4dp."""
    # Rounding keeps the JSON readable. BCCR publishes rates with 2 decimals,
    # but `mean` can produce longer tails — 4dp is comfortable headroom.
    return {
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(mean(values), 4),
    }


def summarize_monthly(rates: list[FlatRate]) -> list[dict[str, Any]]:
    """Group ``rates`` by (year, month) and compute per-month statistics.

    Output shape — one dict per month, ascending::

        {
            "month": "YYYY-MM",
            "buy":  { "min": ..., "max": ..., "mean": ..., "first": ..., "last": ... },
            "sell": { "min": ..., "max": ..., "mean": ..., "first": ..., "last": ... }
        }

    ``first`` / ``last`` are the rates on the *earliest* / *latest* days in the
    input that fall inside that calendar month. If the input range starts or
    ends mid-month, those boundary months are still emitted (partial data is
    still data). Months with no data in the input are omitted.

    Rows whose ``buy`` / ``sell`` are both ``None`` (cached weekend/holiday
    markers) are ignored so statistics are never skewed by phantom zeros.
    """
    # Filter out "empty" rows before grouping.
    real_rows = [r for r in rates if r.buy is not None or r.sell is not None]
    if not real_rows:
        return []

    grouped: dict[str, list[FlatRate]] = defaultdict(list)
    for row in real_rows:
        grouped[_month_key(row)].append(row)

    result: list[dict[str, Any]] = []

    # Iterate in ascending month order — defaultdict preserves insertion
    # order in Python 3.7+, but we sort explicitly for safety.
    for month in sorted(grouped):
        rows = sorted(grouped[month], key=lambda r: r.observed_on)

        buy_values = [r.buy for r in rows if r.buy is not None]
        sell_values = [r.sell for r in rows if r.sell is not None]

        # At least one of the two lists is non-empty because we filtered above.
        buy_bucket = _aggregate(buy_values) if buy_values else {}
        sell_bucket = _aggregate(sell_values) if sell_values else {}

        buy_bucket["first"] = rows[0].buy
        buy_bucket["last"] = rows[-1].buy
        sell_bucket["first"] = rows[0].sell
        sell_bucket["last"] = rows[-1].sell

        result.append({"month": month, "buy": buy_bucket, "sell": sell_bucket})

    return result
