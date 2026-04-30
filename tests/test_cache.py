"""Tests for ``bccr_mcp_server.cache``."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from bccr_mcp_server import cache as cache_mod
from bccr_mcp_server.bccr.models import FlatRate
from bccr_mcp_server.cache import (
    CacheBackedRateService,
    HistoricalRateCache,
)


@pytest.fixture
def freeze_today(monkeypatch: pytest.MonkeyPatch) -> date:
    """Pin ``today_in_costa_rica`` to a fixed date for deterministic tests."""
    fixed = date(2026, 4, 22)
    monkeypatch.setattr(cache_mod, "today_in_costa_rica", lambda: fixed)
    return fixed


def _row(d: date, buy: float | None = 500.0, sell: float | None = 510.0) -> FlatRate:
    return FlatRate(observed_on=d, buy=buy, sell=sell)


# ---------------------------------------------------------------------------
# HistoricalRateCache
# ---------------------------------------------------------------------------

def test_put_and_get_round_trip(freeze_today: date) -> None:
    cache = HistoricalRateCache()
    d = date(2026, 4, 20)
    cache.put(d, _row(d))
    assert cache.get(d) == _row(d)


def test_put_refuses_today(freeze_today: date) -> None:
    cache = HistoricalRateCache()
    cache.put(freeze_today, _row(freeze_today))
    assert cache.get(freeze_today) is None


def test_put_refuses_future_date(freeze_today: date) -> None:
    cache = HistoricalRateCache()
    future = date(2026, 4, 23)
    cache.put(future, _row(future))
    assert cache.get(future) is None


def test_has_range_full_hit(freeze_today: date) -> None:
    cache = HistoricalRateCache()
    for day in (date(2026, 4, 18), date(2026, 4, 19), date(2026, 4, 20)):
        cache.put(day, _row(day))
    assert cache.has_range(date(2026, 4, 18), date(2026, 4, 20))


def test_has_range_partial(freeze_today: date) -> None:
    cache = HistoricalRateCache()
    cache.put(date(2026, 4, 18), _row(date(2026, 4, 18)))
    # 2026-04-19 is missing.
    cache.put(date(2026, 4, 20), _row(date(2026, 4, 20)))
    assert cache.has_range(date(2026, 4, 18), date(2026, 4, 20)) is False


def test_missing_dates_reports_gaps(freeze_today: date) -> None:
    cache = HistoricalRateCache()
    cache.put(date(2026, 4, 18), _row(date(2026, 4, 18)))
    # 19 and 20 missing, 21 missing too
    missing = cache.missing_dates(date(2026, 4, 18), date(2026, 4, 21))
    assert missing == [date(2026, 4, 19), date(2026, 4, 20), date(2026, 4, 21)]


def test_has_range_rejects_today_boundary(freeze_today: date) -> None:
    cache = HistoricalRateCache()
    # Even if we tried to put today (which is refused), has_range must return False
    # because today is never served from cache.
    assert cache.has_range(date(2026, 4, 18), freeze_today) is False


# ---------------------------------------------------------------------------
# CacheBackedRateService
# ---------------------------------------------------------------------------

class FakeClient:
    """Stand-in for ``BccrClient`` that records calls and returns canned rows."""

    def __init__(self, rows_by_range: dict[tuple[date, date], list[FlatRate]]) -> None:
        self._rows = rows_by_range
        self.calls: list[tuple[date, date]] = []

    async def fetch_buy_sell(self, start: date, end: date) -> list[FlatRate]:
        self.calls.append((start, end))
        return self._rows.get((start, end), [])


async def test_second_identical_call_served_from_cache(freeze_today: date) -> None:
    """A repeat query should not hit the fake client again."""
    d = date(2026, 4, 20)
    client: Any = FakeClient({(d, d): [_row(d)]})
    cache = HistoricalRateCache()
    service = CacheBackedRateService(client=client, cache=cache)

    first = await service.fetch_range(d, d)
    second = await service.fetch_range(d, d)

    assert first == second
    assert len(client.calls) == 1


async def test_partial_overlap_fetches_only_missing(freeze_today: date) -> None:
    """After priming [18..19], asking for [19..21] fetches only [20..21]."""
    d18, d19, d20, d21 = (
        date(2026, 4, 18),
        date(2026, 4, 19),
        date(2026, 4, 20),
        date(2026, 4, 21),
    )

    client: Any = FakeClient(
        {
            (d18, d19): [_row(d18), _row(d19)],
            (d20, d21): [_row(d20), _row(d21)],
        }
    )
    cache = HistoricalRateCache()
    service = CacheBackedRateService(client=client, cache=cache)

    await service.fetch_range(d18, d19)
    client.calls.clear()

    rows = await service.fetch_range(d19, d21)

    assert [r.observed_on for r in rows] == [d19, d20, d21]
    assert client.calls == [(d20, d21)]


async def test_today_always_fetched_live(freeze_today: date) -> None:
    """A range ending on today must trigger a live fetch for today."""
    d18 = date(2026, 4, 18)
    today = freeze_today

    client: Any = FakeClient(
        {
            (d18, date(2026, 4, 21)): [_row(d18)],
            (today, today): [_row(today, buy=600.0, sell=610.0)],
        }
    )
    cache = HistoricalRateCache()
    service = CacheBackedRateService(client=client, cache=cache)

    rows = await service.fetch_range(d18, today)

    # Second call in ``calls`` is the today-live fetch.
    assert (today, today) in client.calls
    assert rows[-1].observed_on == today
