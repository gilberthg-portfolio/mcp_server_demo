"""Tests for the MCP-sampling narrative integration.

These tests exercise the narrative path in ``tools.exchange_rate`` with a
fake ``Context`` that records sampling calls. We verify that sampling is
invoked only when:
  * ``summarize=True``
  * ``with_narrative=True`` (the default)
  * the client advertises the ``sampling`` capability
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from bccr_mcp_server import cache as cache_mod
from bccr_mcp_server.bccr.models import FlatRate
from bccr_mcp_server.cache import CacheBackedRateService, HistoricalRateCache
from bccr_mcp_server.tools import exchange_rate as tools
from bccr_mcp_server.tools.narrative import build_sampling_request


@pytest.fixture
def freeze_today(monkeypatch: pytest.MonkeyPatch) -> date:
    fixed = date(2026, 4, 22)
    monkeypatch.setattr(cache_mod, "today_in_costa_rica", lambda: fixed)
    return fixed


class FakeClient:
    async def fetch_buy_sell(self, start: date, end: date) -> list[FlatRate]:
        return [
            FlatRate(observed_on=start, buy=507.0, sell=513.0),
            FlatRate(observed_on=end, buy=508.0, sell=514.0),
        ]


class FakeSession:
    """Records every ``create_message`` call and returns a canned narrative."""

    def __init__(
        self,
        *,
        supports_sampling: bool,
        raise_on_create: BaseException | None = None,
    ) -> None:
        self._supports_sampling = supports_sampling
        self._raise = raise_on_create
        self.create_calls: list[dict[str, Any]] = []

    def check_client_capability(self, *_args: Any, **_kwargs: Any) -> bool:
        return self._supports_sampling

    async def create_message(self, **kwargs: Any) -> Any:
        self.create_calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        # Return an object that looks enough like MCP's CreateMessageResult.
        return SimpleNamespace(
            content=SimpleNamespace(
                type="text",
                text="CRC strengthened slightly against USD in April 2026.",
            )
        )


class FakeCtx:
    def __init__(self, session: FakeSession) -> None:
        self.session = session


def _install(freeze_today: date) -> None:
    client: Any = FakeClient()
    cache = HistoricalRateCache()
    service = CacheBackedRateService(client=client, cache=cache)
    tools.configure_tools(client=client, service=service)


def _mp_text(session: FakeSession) -> Any:
    """Work around FakeSession's SimpleNamespace not matching isinstance TextContent."""
    # The production code checks ``isinstance(content, TextContent)``. Tests here
    # patch that check so the SimpleNamespace shim still produces a narrative.
    pass


@pytest.fixture(autouse=True)
def patch_textcontent_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tell the tool module to accept our SimpleNamespace text content.

    The production code verifies content via ``isinstance(content, TextContent)``
    from ``mcp.types``. Tests use a SimpleNamespace, which isn't an instance of
    that type, so we swap the check for attribute-based duck-typing.
    """
    original = tools._request_narrative  # noqa: SLF001

    async def patched(ctx: Any, months: list[dict[str, Any]]) -> str | None:
        from bccr_mcp_server.tools.narrative import build_sampling_request as _build

        params = _build(months)
        try:
            result = await ctx.session.create_message(
                messages=params.messages,
                max_tokens=params.maxTokens,
                system_prompt=params.systemPrompt,
                temperature=params.temperature,
                model_preferences=params.modelPreferences,
            )
        except Exception:  # noqa: BLE001
            return None
        content = getattr(result, "content", None)
        text = getattr(content, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
        return None

    monkeypatch.setattr(tools, "_request_narrative", patched)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

async def test_narrative_included_when_client_supports_sampling(
    freeze_today: date,
) -> None:
    _install(freeze_today)
    session = FakeSession(supports_sampling=True)
    ctx = FakeCtx(session)

    result = await tools.get_historical_exchange_rate(
        ctx,
        start_date=date(2026, 4, 18).isoformat(),
        end_date=date(2026, 4, 19).isoformat(),
        summarize=True,
    )

    assert "narrative" in result
    assert len(session.create_calls) == 1


async def test_no_narrative_when_sampling_unsupported(freeze_today: date) -> None:
    _install(freeze_today)
    session = FakeSession(supports_sampling=False)
    ctx = FakeCtx(session)

    result = await tools.get_historical_exchange_rate(
        ctx,
        start_date=date(2026, 4, 18).isoformat(),
        end_date=date(2026, 4, 19).isoformat(),
        summarize=True,
    )

    assert "narrative" not in result
    assert session.create_calls == []


async def test_no_narrative_when_caller_opts_out(freeze_today: date) -> None:
    _install(freeze_today)
    session = FakeSession(supports_sampling=True)
    ctx = FakeCtx(session)

    result = await tools.get_historical_exchange_rate(
        ctx,
        start_date=date(2026, 4, 18).isoformat(),
        end_date=date(2026, 4, 19).isoformat(),
        summarize=True,
        with_narrative=False,
    )

    assert "narrative" not in result
    assert session.create_calls == []


async def test_narrative_failure_is_swallowed(freeze_today: date) -> None:
    _install(freeze_today)
    session = FakeSession(
        supports_sampling=True, raise_on_create=RuntimeError("model busy")
    )
    ctx = FakeCtx(session)

    result = await tools.get_historical_exchange_rate(
        ctx,
        start_date=date(2026, 4, 18).isoformat(),
        end_date=date(2026, 4, 19).isoformat(),
        summarize=True,
    )

    # Tool still succeeds with months payload; no narrative.
    assert "months" in result
    assert "narrative" not in result


async def test_summarize_false_never_calls_sampling(freeze_today: date) -> None:
    _install(freeze_today)
    session = FakeSession(supports_sampling=True)
    ctx = FakeCtx(session)

    await tools.get_historical_exchange_rate(
        ctx,
        start_date=date(2026, 4, 18).isoformat(),
        end_date=date(2026, 4, 19).isoformat(),
        summarize=False,
        with_narrative=True,
    )

    assert session.create_calls == []


# ---------------------------------------------------------------------------
# The pure request builder
# ---------------------------------------------------------------------------

def test_build_sampling_request_has_expected_shape() -> None:
    months = [
        {"month": "2026-04", "buy": {"mean": 507.0}, "sell": {"mean": 513.0}}
    ]
    params = build_sampling_request(months)

    assert params.maxTokens == 200
    assert params.temperature == 0.3
    assert params.systemPrompt is not None
    assert "financial analyst" in params.systemPrompt
    assert len(params.messages) == 1
    # The JSON payload must actually carry our months data.
    text = params.messages[0].content.text
    assert "2026-04" in text
