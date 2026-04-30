"""Tests for ``bccr_mcp_server.tools.exchange_rate`` tool handlers.

These tests exercise the tool functions directly rather than going through
the MCP transport. We fake the ``BccrClient`` and ``Context`` because the
rate service doesn't know (or care) about either one.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from bccr_mcp_server import cache as cache_mod
from bccr_mcp_server.bccr.models import FlatRate
from bccr_mcp_server.cache import CacheBackedRateService, HistoricalRateCache
from bccr_mcp_server.errors import UpstreamError
from bccr_mcp_server.tools import exchange_rate as tools


@pytest.fixture
def freeze_today(monkeypatch: pytest.MonkeyPatch) -> date:
    fixed = date(2026, 4, 22)
    monkeypatch.setattr(cache_mod, "today_in_costa_rica", lambda: fixed)
    return fixed


class FakeClient:
    """Configurable fake for ``BccrClient``.

    ``rows_by_range`` maps a (start, end) tuple to the list of rows to return.
    ``raise_exc`` optionally raises instead — used for error-path tests.
    """

    def __init__(
        self,
        rows_by_range: dict[tuple[date, date], list[FlatRate]] | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._rows = rows_by_range or {}
        self._raise = raise_exc
        self.calls: list[tuple[date, date]] = []

    async def fetch_buy_sell(self, start: date, end: date) -> list[FlatRate]:
        self.calls.append((start, end))
        if self._raise is not None:
            raise self._raise
        return self._rows.get((start, end), [])


class FakeCtx:
    """Minimal stand-in for FastMCP's ``Context``.

    Tests that never exercise sampling can pass this; tests that do need
    sampling should use ``FakeCtxWithSampling`` below.
    """

    class _Session:
        def check_client_capability(self, *args: Any, **kwargs: Any) -> bool:
            return False

    def __init__(self) -> None:
        self.session = self._Session()


def _install_service(client: Any, freeze_today: date) -> None:
    cache = HistoricalRateCache()
    service = CacheBackedRateService(client=client, cache=cache)
    tools.configure_tools(client=client, service=service)


# ---------------------------------------------------------------------------
# get_current_exchange_rate
# ---------------------------------------------------------------------------

async def test_current_happy_path(freeze_today: date) -> None:
    client = FakeClient(
        {(freeze_today, freeze_today): [FlatRate(observed_on=freeze_today, buy=507.2, sell=513.5)]}
    )
    _install_service(client, freeze_today)

    result = await tools.get_current_exchange_rate(FakeCtx())

    assert result == {"date": freeze_today.isoformat(), "buy": 507.2, "sell": 513.5}


async def test_current_no_data_returns_message(freeze_today: date) -> None:
    client = FakeClient({(freeze_today, freeze_today): []})
    _install_service(client, freeze_today)

    result = await tools.get_current_exchange_rate(FakeCtx())

    assert result["buy"] is None
    assert result["sell"] is None
    assert "message" in result


async def test_upstream_failure_becomes_value_error(freeze_today: date) -> None:
    client = FakeClient(raise_exc=UpstreamError(status=503, detail="down"))
    _install_service(client, freeze_today)

    with pytest.raises(ValueError, match="temporarily unavailable"):
        await tools.get_current_exchange_rate(FakeCtx())


# ---------------------------------------------------------------------------
# get_historical_exchange_rate — validation
# ---------------------------------------------------------------------------

async def test_historical_rejects_malformed_date(freeze_today: date) -> None:
    client = FakeClient()
    _install_service(client, freeze_today)

    with pytest.raises(ValueError, match="ISO-8601"):
        await tools.get_historical_exchange_rate(
            FakeCtx(), start_date="21/04/2026", end_date="2026-04-21"
        )
    # Must not have called BCCR.
    assert client.calls == []


async def test_historical_rejects_end_before_start(freeze_today: date) -> None:
    client = FakeClient()
    _install_service(client, freeze_today)

    with pytest.raises(ValueError, match="on or after"):
        await tools.get_historical_exchange_rate(
            FakeCtx(), start_date="2026-04-21", end_date="2026-04-20"
        )


async def test_historical_rejects_range_over_cap(freeze_today: date) -> None:
    client = FakeClient()
    _install_service(client, freeze_today)

    with pytest.raises(ValueError, match="3660"):
        await tools.get_historical_exchange_rate(
            FakeCtx(), start_date="2015-01-01", end_date="2026-04-22"
        )


# ---------------------------------------------------------------------------
# get_historical_exchange_rate — happy paths
# ---------------------------------------------------------------------------

async def test_historical_daily_happy_path(freeze_today: date) -> None:
    d1 = date(2026, 4, 18)
    d2 = date(2026, 4, 19)
    client = FakeClient(
        {
            (d1, d2): [
                FlatRate(observed_on=d1, buy=507.0, sell=513.0),
                FlatRate(observed_on=d2, buy=508.0, sell=514.0),
            ]
        }
    )
    _install_service(client, freeze_today)

    result = await tools.get_historical_exchange_rate(
        FakeCtx(), start_date=d1.isoformat(), end_date=d2.isoformat()
    )

    assert "rates" in result
    assert len(result["rates"]) == 2
    assert result["rates"][0] == {"date": d1.isoformat(), "buy": 507.0, "sell": 513.0}


async def test_historical_weekend_only_returns_message(freeze_today: date) -> None:
    # Saturday / Sunday — BCCR returns nothing.
    start = date(2026, 4, 18)  # Sat
    end = date(2026, 4, 19)    # Sun
    client = FakeClient({(start, end): []})
    _install_service(client, freeze_today)

    result = await tools.get_historical_exchange_rate(
        FakeCtx(), start_date=start.isoformat(), end_date=end.isoformat()
    )

    assert result["rates"] == []
    assert "message" in result


async def test_historical_summarize_happy_path(freeze_today: date) -> None:
    d1 = date(2026, 4, 18)
    d2 = date(2026, 4, 19)
    client = FakeClient(
        {
            (d1, d2): [
                FlatRate(observed_on=d1, buy=507.0, sell=513.0),
                FlatRate(observed_on=d2, buy=508.0, sell=514.0),
            ]
        }
    )
    _install_service(client, freeze_today)

    result = await tools.get_historical_exchange_rate(
        FakeCtx(),
        start_date=d1.isoformat(),
        end_date=d2.isoformat(),
        summarize=True,
    )

    assert "months" in result
    assert len(result["months"]) == 1
    assert result["months"][0]["month"] == "2026-04"
    # FakeCtx does not support sampling, so no narrative.
    assert "narrative" not in result


async def test_historical_summarize_no_data(freeze_today: date) -> None:
    start = date(2026, 4, 18)
    end = date(2026, 4, 19)
    client = FakeClient({(start, end): []})
    _install_service(client, freeze_today)

    result = await tools.get_historical_exchange_rate(
        FakeCtx(),
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        summarize=True,
    )

    assert result["months"] == []
    assert "message" in result
