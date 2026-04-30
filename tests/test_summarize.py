"""Tests for ``bccr_mcp_server.tools.summarize.summarize_monthly``."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from bccr_mcp_server.bccr.models import FlatRate
from bccr_mcp_server.tools.summarize import summarize_monthly


def _row(d: date, buy: float, sell: float) -> FlatRate:
    return FlatRate(observed_on=d, buy=buy, sell=sell)


def test_empty_input_returns_empty_list() -> None:
    assert summarize_monthly([]) == []


def test_single_day_produces_one_bucket() -> None:
    d = date(2026, 4, 21)
    result = summarize_monthly([_row(d, 507.0, 513.0)])
    assert len(result) == 1
    bucket = result[0]
    assert bucket["month"] == "2026-04"
    assert bucket["buy"] == {
        "min": 507.0, "max": 507.0, "mean": 507.0, "first": 507.0, "last": 507.0
    }
    assert bucket["sell"]["first"] == 513.0


def test_range_within_one_month_min_max_mean() -> None:
    rows = [
        _row(date(2026, 4, 1), 500.0, 510.0),
        _row(date(2026, 4, 2), 502.0, 512.0),
        _row(date(2026, 4, 3), 504.0, 514.0),
    ]
    result = summarize_monthly(rows)
    assert len(result) == 1
    buy = result[0]["buy"]
    assert buy["min"] == 500.0
    assert buy["max"] == 504.0
    assert buy["mean"] == pytest.approx(502.0)
    assert buy["first"] == 500.0
    assert buy["last"] == 504.0


def test_range_crossing_month_boundary_produces_two_buckets() -> None:
    rows = [
        _row(date(2026, 3, 30), 498.0, 508.0),
        _row(date(2026, 3, 31), 499.0, 509.0),
        _row(date(2026, 4, 1), 500.0, 510.0),
        _row(date(2026, 4, 2), 501.0, 511.0),
    ]
    result = summarize_monthly(rows)
    assert [b["month"] for b in result] == ["2026-03", "2026-04"]
    assert result[0]["buy"]["first"] == 498.0
    assert result[0]["buy"]["last"] == 499.0
    assert result[1]["buy"]["first"] == 500.0


def test_rows_with_all_none_are_ignored() -> None:
    # Weekend/holiday marker rows should not skew stats or produce buckets.
    rows = [
        FlatRate(observed_on=date(2026, 4, 4), buy=None, sell=None),  # Saturday
        FlatRate(observed_on=date(2026, 4, 5), buy=None, sell=None),  # Sunday
    ]
    assert summarize_monthly(rows) == []


def test_ten_year_span_produces_one_bucket_per_month_with_data() -> None:
    # Generate one row per month for 10 years (120 months).
    rows: list[FlatRate] = []
    d = date(2016, 1, 15)
    while d.year < 2026:
        rows.append(_row(d, 500.0 + d.year - 2016, 510.0 + d.year - 2016))
        # Advance roughly one month — use day 15 to avoid month-length issues.
        year, month = d.year, d.month
        if month == 12:
            d = date(year + 1, 1, 15)
        else:
            d = date(year, month + 1, 15)

    result = summarize_monthly(rows)
    assert len(result) == 120
    # First and last buckets carry the expected years.
    assert result[0]["month"].startswith("2016-")
    assert result[-1]["month"].startswith("2025-")


def test_ordered_ascending_by_month() -> None:
    # Shuffle input order to ensure the function sorts internally.
    rows = [
        _row(date(2026, 4, 2), 501.0, 511.0),
        _row(date(2026, 3, 30), 498.0, 508.0),
        _row(date(2026, 4, 1), 500.0, 510.0),
        _row(date(2026, 3, 31), 499.0, 509.0),
    ]
    result = summarize_monthly(rows)
    assert [b["month"] for b in result] == ["2026-03", "2026-04"]
