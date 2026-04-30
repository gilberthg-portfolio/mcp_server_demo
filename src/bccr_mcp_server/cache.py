"""In-memory cache of historical daily exchange rates.

Historical BCCR rates are immutable once published — the rate for
2020-03-15 never changes. That makes the caching policy trivial: once a
rate enters the cache it stays until the process dies. No TTL, no eviction.

Two kinds of "date" we care about:
    * *historical*  -- strictly before today in America/Costa_Rica. Cached.
    * *today*       -- today in America/Costa_Rica. **Never** cached, because
                       the rate can flip from "not yet published" to
                       "published" during a session.

The ``CacheBackedRateService`` class at the bottom is the front door for the
tool layer: it handles "check cache, fetch missing, populate, merge" in one
call.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from zoneinfo import ZoneInfo

# --- Python idiom: `zoneinfo.ZoneInfo` (stdlib, 3.9+) -----------------------
# `ZoneInfo("America/Costa_Rica")` gives us a timezone object backed by the
# IANA database bundled with Python. Costa Rica has no DST so the offset is
# a stable UTC-06:00, but using ZoneInfo keeps us honest if that ever changes.

from .bccr.client import BccrClient
from .bccr.models import FlatRate

log = logging.getLogger(__name__)

COSTA_RICA_TZ = ZoneInfo("America/Costa_Rica")


def today_in_costa_rica() -> date:
    """Return the current calendar date as seen in Costa Rica.

    Isolated in one function so tests can monkey-patch this single symbol to
    simulate any wall-clock date.
    """
    # --- Python idiom: `from datetime import datetime` inside a function ----
    # Keeping the import local makes it trivial for tests to patch
    # `bccr_mcp_server.cache.today_in_costa_rica` directly without worrying
    # about `datetime` being used elsewhere.
    from datetime import datetime

    return datetime.now(tz=COSTA_RICA_TZ).date()


class HistoricalRateCache:
    """Process-local dict keyed by ``date``.

    A deliberately small API: get / put / range-query helpers. Everything is
    pure in-memory data structures — no locking because Python's asyncio
    event loop is single-threaded, and we don't share the cache across
    processes.
    """

    def __init__(self) -> None:
        self._store: dict[date, FlatRate] = {}

    def get(self, d: date) -> FlatRate | None:
        """Return the cached rate for ``d`` or ``None`` if absent."""
        return self._store.get(d)

    def put(self, d: date, rate: FlatRate) -> None:
        """Store ``rate`` in the cache, keyed by ``d``.

        Today's date is rejected — see module docstring for why.
        """
        if d >= today_in_costa_rica():
            # Silently ignore: the caller may pass a list that includes
            # today, and we don't want that to raise. Logging at debug
            # level is enough for troubleshooting.
            log.debug("Refusing to cache %s (today or future).", d)
            return
        self._store[d] = rate

    def has_range(self, start: date, end: date) -> bool:
        """Return True if *every* calendar date in [start, end] is cached.

        Note: this includes weekends / holidays, which BCCR doesn't publish.
        For those we expect to find a ``FlatRate`` with ``buy=None`` and
        ``sell=None`` (populated during the first fetch that spanned them).
        """
        cutoff = today_in_costa_rica()
        if end >= cutoff:
            # Can't fully serve ranges that extend into today from cache.
            return False
        current = start
        while current <= end:
            if current not in self._store:
                return False
            current += timedelta(days=1)
        return True

    def missing_dates(self, start: date, end: date) -> list[date]:
        """Return the calendar dates in [start, end] we haven't cached yet.

        Dates on/after today are reported as missing regardless, so the
        caller is forced to fetch them fresh.
        """
        cutoff = today_in_costa_rica()
        missing: list[date] = []
        current = start
        while current <= end:
            if current >= cutoff or current not in self._store:
                missing.append(current)
            current += timedelta(days=1)
        return missing


class CacheBackedRateService:
    """Fetch historical rates, preferring the cache when possible.

    This class is the single collaborator the tool layer talks to for
    historical data. It hides the "check cache, fetch only the missing
    sub-range, merge" dance behind one method.
    """

    def __init__(self, client: BccrClient, cache: HistoricalRateCache) -> None:
        self._client = client
        self._cache = cache

    async def fetch_range(self, start: date, end: date) -> list[FlatRate]:
        """Return all cached + freshly-fetched rates in [start, end] sorted by date.

        Strategy:
            * Split the request at today-1: the "historical" half can
              potentially come from the cache, the "today+future" half
              always hits BCCR.
            * For the historical half, ask the cache for missing dates. If
              any are missing, fetch the minimal contiguous sub-range that
              covers them (one BCCR call regardless of gap pattern).
            * Populate the cache with every fresh point (including days BCCR
              had no data for — caching a None prevents a second round-trip).
            * Merge and return.
        """
        cutoff = today_in_costa_rica()

        # --- Historical half ------------------------------------------------
        hist_end = min(end, cutoff - timedelta(days=1))
        hist_rows: list[FlatRate] = []

        if start <= hist_end:
            missing = self._cache.missing_dates(start, hist_end)
            if missing:
                # One BCCR call covers the smallest contiguous range that
                # includes every missing date. Any already-cached dates
                # inside this range get refreshed — harmless (immutable).
                fetch_start = missing[0]
                fetch_end = missing[-1]
                fresh = await self._client.fetch_buy_sell(fetch_start, fetch_end)

                # Build a set for O(1) "did BCCR return this date?" lookup.
                fresh_by_date = {r.observed_on: r for r in fresh}

                # Populate cache with whatever BCCR gave us for the missing
                # dates. For dates BCCR did not return (weekend/holiday) we
                # still cache a row with both values = None, so repeat
                # queries know we already asked and got nothing.
                current = fetch_start
                while current <= fetch_end:
                    row = fresh_by_date.get(
                        current,
                        FlatRate(observed_on=current, buy=None, sell=None),
                    )
                    self._cache.put(current, row)
                    current += timedelta(days=1)

            # Assemble the historical half strictly from the cache — every
            # date is now populated (either with real data or a None row).
            current = start
            while current <= hist_end:
                row = self._cache.get(current)
                if row is not None and (row.buy is not None or row.sell is not None):
                    hist_rows.append(row)
                current += timedelta(days=1)

        # --- Today / future half --------------------------------------------
        today_rows: list[FlatRate] = []
        if end >= cutoff:
            today_rows = await self._client.fetch_buy_sell(
                max(start, cutoff), end
            )
            # Do NOT cache these — they're today or later.

        # Already sorted by construction (both halves are ascending).
        return hist_rows + today_rows
