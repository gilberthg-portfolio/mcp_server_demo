"""MCP tool implementations for BCCR exchange-rate queries.

Two tools are exposed:

* ``get_current_exchange_rate()`` — today's USD/CRC (series 317 buy, 318 sell).
  Bypasses the cache; today is never cached (see ``cache.py``).

* ``get_historical_exchange_rate(start_date, end_date, summarize, with_narrative)``
  — daily or monthly-aggregated rates across a date range up to 3,660 days.
  Optionally attaches a short LLM-generated narrative via MCP sampling.

Both functions take an ``mcp.server.fastmcp.Context`` so they can access the
session (needed for sampling) and logger. FastMCP injects ``Context`` when
it is declared as the *first* parameter of a tool function.
"""

from __future__ import annotations

import logging
from datetime import date
from functools import wraps
from typing import Any, Awaitable, Callable

# --- MCP SDK imports --------------------------------------------------------
from mcp.server.fastmcp import Context
from mcp.types import ClientCapabilities, SamplingCapability, TextContent

from .. import cache as _cache_mod  # imported as a module so that test
                                    # monkeypatching of `today_in_costa_rica`
                                    # in `bccr_mcp_server.cache` is visible
                                    # here. Importing the function directly
                                    # would capture the pre-patch reference.
from ..bccr.client import BccrClient
from ..cache import CacheBackedRateService
from ..errors import BccrMcpError, ValidationError, to_mcp_tool_error
from .narrative import build_sampling_request
from .summarize import summarize_monthly


log = logging.getLogger(__name__)

# --- Configuration constants ------------------------------------------------
# The maximum span we'll accept on a single call. 3,660 days ≈ 10 years;
# matches the spec's cap requirement.
MAX_RANGE_DAYS = 3660

# --- Module-level dependency handles ----------------------------------------
# These are populated by ``configure_tools`` during server startup. We keep
# them at module scope (rather than passing them to every call) because
# FastMCP's tool functions are registered by reference — there's no clean
# spot to pipe a per-request dependency bundle. A bound service is good
# enough for a single-process, single-client MCP server.
_client: BccrClient | None = None
_rate_service: CacheBackedRateService | None = None


def configure_tools(
    client: BccrClient,
    service: CacheBackedRateService,
) -> None:
    """Install the runtime collaborators before the server starts.

    Called from ``__main__.main`` right after settings are loaded.
    """
    global _client, _rate_service
    _client = client
    _rate_service = service


# ---------------------------------------------------------------------------
# Error-to-ToolError wrapper
# ---------------------------------------------------------------------------

# --- Python idiom: decorator factory + `functools.wraps` --------------------
# A decorator is a function that returns a modified version of the function
# it wraps. Here we use it to catch our custom exceptions once and emit a
# clean ``ValueError`` containing the MCP-friendly message. FastMCP surfaces
# raised exceptions to the client as tool errors, so raising is the correct
# shape. ``@wraps(fn)`` copies over the wrapped function's name and docstring
# so the MCP tool metadata remains accurate.

def _translate_errors(
    fn: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Translate ``BccrMcpError`` subclasses into user-facing ``ValueError``.

    Any other exception propagates unchanged — they're bugs worth surfacing.
    """

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except BccrMcpError as exc:
            raise ValueError(to_mcp_tool_error(exc)) from exc

    return wrapper


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _parse_iso_date(value: str, *, field: str) -> date:
    """Parse an ISO-8601 ``YYYY-MM-DD`` string or raise ``ValidationError``."""
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValidationError(
            f"Argument `{field}` must be an ISO-8601 date string "
            f"(YYYY-MM-DD). Received: {value!r}."
        ) from exc


def _validate_range(start: date, end: date) -> None:
    """Enforce start <= end and the 3,660-day cap."""
    if start > end:
        raise ValidationError(
            "`end_date` must be on or after `start_date`."
        )
    span_days = (end - start).days
    if span_days > MAX_RANGE_DAYS:
        raise ValidationError(
            f"Date range too large: requested {span_days} days, maximum is "
            f"{MAX_RANGE_DAYS} days (~10 years). Please narrow the range or "
            f"issue multiple calls."
        )


# ---------------------------------------------------------------------------
# Tool: get_current_exchange_rate
# ---------------------------------------------------------------------------

@_translate_errors
async def get_current_exchange_rate(ctx: Context) -> dict[str, Any]:
    """Return today's USD↔CRC buy and sell rates (America/Costa_Rica business day).

    Response shape::

        {
            "date": "YYYY-MM-DD",
            "buy":  <float|null>,
            "sell": <float|null>,
            "message": "<optional — only when BCCR has no rate for today>"
        }
    """
    if _client is None:
        raise RuntimeError("Tools are not configured; call configure_tools() first.")

    today = _cache_mod.today_in_costa_rica()
    rows = await _client.fetch_buy_sell(today, today)

    if not rows:
        return {
            "date": today.isoformat(),
            "buy": None,
            "sell": None,
            "message": (
                "BCCR has not yet published an exchange rate for "
                f"{today.isoformat()} (it may be a weekend, a holiday, or "
                "the rate is yet to be released today)."
            ),
        }

    # If BCCR returns data, there's always exactly one row for today.
    rate = rows[0]
    return rate.to_response_dict()


# ---------------------------------------------------------------------------
# Tool: get_historical_exchange_rate
# ---------------------------------------------------------------------------

@_translate_errors
async def get_historical_exchange_rate(
    ctx: Context,
    start_date: str,
    end_date: str,
    summarize: bool = False,
    with_narrative: bool = True,
) -> dict[str, Any]:
    """Return USD↔CRC rates for the inclusive date range [start_date, end_date].

    Args:
        start_date: ISO-8601 (YYYY-MM-DD). Inclusive.
        end_date:   ISO-8601 (YYYY-MM-DD). Inclusive.
        summarize:  When ``True``, return one monthly bucket per month
                    (min / max / mean / first / last for buy and sell) in a
                    ``"months"`` array. When ``False`` (default), return daily
                    rows in a ``"rates"`` array.
        with_narrative:
                    Only consulted when ``summarize=True``. When ``True`` and
                    the connected client supports MCP sampling, a short
                    human-readable narrative of the trend is attached under
                    the ``"narrative"`` key. Set to ``False`` to skip the
                    sampling round-trip entirely (also skipped automatically
                    if the client doesn't support sampling).

    Date range is capped at 3,660 days (~10 years).
    """
    if _rate_service is None:
        raise RuntimeError("Tools are not configured; call configure_tools() first.")

    start = _parse_iso_date(start_date, field="start_date")
    end = _parse_iso_date(end_date, field="end_date")
    _validate_range(start, end)

    rates = await _rate_service.fetch_range(start, end)

    # --- daily path ---------------------------------------------------------
    if not summarize:
        if not rates:
            return {
                "rates": [],
                "message": (
                    f"BCCR has no published data in the range "
                    f"{start.isoformat()} .. {end.isoformat()} "
                    "(weekends and holidays are always empty)."
                ),
            }
        return {"rates": [r.to_response_dict() for r in rates]}

    # --- summarize path -----------------------------------------------------
    months = summarize_monthly(rates)
    if not months:
        return {
            "months": [],
            "message": (
                f"BCCR has no published data in the range "
                f"{start.isoformat()} .. {end.isoformat()} "
                "(weekends and holidays are always empty)."
            ),
        }

    response: dict[str, Any] = {"months": months}

    # Try to attach a narrative only when the caller hasn't opted out AND
    # the connected client advertises the sampling capability. Failure at
    # any point below is silently swallowed — narrative is additive.
    if with_narrative and _client_supports_sampling(ctx):
        narrative = await _request_narrative(ctx, months)
        if narrative is not None:
            response["narrative"] = narrative

    return response


# ---------------------------------------------------------------------------
# Sampling helpers — kept module-private and heavily commented because the
# MCP sampling API is the most "demo-worthy" feature of this project.
# ---------------------------------------------------------------------------

def _client_supports_sampling(ctx: Context) -> bool:
    """Ask the session whether the current client advertises ``sampling``.

    The MCP protocol negotiates capabilities at startup: the client sends a
    list of things it supports (roots, sampling, ...) and the server checks
    against that list before exercising those features. This helper returns
    ``False`` on any error, which keeps the narrative path strictly additive.
    """
    try:
        return bool(
            ctx.session.check_client_capability(
                ClientCapabilities(sampling=SamplingCapability())
            )
        )
    except Exception:  # noqa: BLE001 — any failure means "no narrative"
        log.debug("check_client_capability raised; treating as no sampling.")
        return False


async def _request_narrative(
    ctx: Context,
    months: list[dict[str, Any]],
) -> str | None:
    """Issue the sampling request and return the text, or ``None`` on any failure.

    We deliberately catch broad exceptions here:
      * The client may reject the sampling prompt at the user-consent layer.
      * The underlying LLM call may time out or rate-limit.
      * A protocol-level error would otherwise break a successful tool call.
    In all those cases the structured months payload is still valuable on
    its own; the narrative is icing.
    """
    params = build_sampling_request(months)

    try:
        # ``create_message`` sends the sampling/createMessage request to
        # the client and awaits the LLM response. The return value carries
        # ``content`` which is either a TextContent or ImageContent.
        result = await ctx.session.create_message(
            messages=params.messages,
            max_tokens=params.maxTokens,
            system_prompt=params.systemPrompt,
            temperature=params.temperature,
            model_preferences=params.modelPreferences,
        )
    except Exception:  # noqa: BLE001
        # We do NOT log the prompt contents here. Sampling prompts can in
        # principle leak user context; keep the log line opaque.
        log.warning("Sampling request failed; omitting narrative.", exc_info=True)
        return None

    # The LLM's reply lives in ``result.content``. We only accept text.
    content = getattr(result, "content", None)
    if isinstance(content, TextContent):
        text = content.text.strip()
        return text or None
    # Non-text content (image, unsupported) → no narrative.
    return None
