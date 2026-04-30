# 03 — Code walkthrough

This file follows the **lifecycle of a single MCP tool call** through the codebase. Every section below corresponds to one module, in the order that module gets involved when a client calls, say, `get_historical_exchange_rate(...)`.

Cross-references to concept names use `[concept]` notation and all point into [`02-python-concepts.md`](02-python-concepts.md). For MCP-specific vocabulary (tool, sampling, capability), see [`01-mcp-primer.md`](01-mcp-primer.md).

---

## 0. The directory at a glance

```
src/bccr_mcp_server/
├── __init__.py            # package metadata
├── __main__.py            # entry point (python -m bccr_mcp_server)
├── server.py              # FastMCP wiring
├── config.py              # env-var loading
├── errors.py              # exception hierarchy
├── cache.py               # in-memory historical rate cache
├── bccr/
│   ├── client.py          # async HTTP client for BCCR
│   └── models.py          # pydantic models for BCCR payloads
└── tools/
    ├── exchange_rate.py   # the two MCP tools
    ├── summarize.py       # monthly aggregator (pure)
    └── narrative.py       # sampling request builder (pure)
```

## 1. `__main__.py` — startup

When Claude Desktop spawns the server (see [stdio transport](01-mcp-primer.md#transport-stdio)), Python runs `bccr_mcp_server/__main__.py::main()`.

1. Logging is configured to write to **stderr**, not stdout — stdout is reserved for the JSON-RPC protocol.
2. `load_settings()` reads the environment. On failure we print to stderr and `sys.exit(1)` — this surfaces as a clear error in Claude Desktop's connection panel.
3. `asyncio.run(_run())` hands off to the async body. [[async/await](02-python-concepts.md#5-async--await)]
4. Inside `_run()` we open a [`BccrClient` async context manager](02-python-concepts.md#6-async-context-managers--async-with-__aenter__--__aexit__), build a `HistoricalRateCache` and a `CacheBackedRateService`, wire them into the tools module via `configure_tools(...)` [[module singletons](02-python-concepts.md#12-module-level-singletons--closure-di)], and call `mcp.run_stdio_async()`.

## 2. `config.py` — reading the bearer token

One responsibility: produce a `Settings` [[@dataclass(frozen=True)](02-python-concepts.md#3-dataclasses--dataclassfrozentrue)] with a validated `bccr_token` plus a `bccr_base_url`. It does this by:

- Calling `dotenv.load_dotenv(override=False)` (so Claude Desktop's injected `env` beats local `.env`).
- Stripping and checking `BCCR_TOKEN`. An empty/missing value raises `ConfigurationError` with a user-actionable message.

`BCCR_BASE_URL` is exposed as an override so tests can point the client elsewhere without monkey-patching.

## 3. `errors.py` — the exception hierarchy

Four classes:

- `BccrMcpError` — the base. Anything caught-and-rethrown downstream is one of these.
- `ConfigurationError` — startup-only.
- `ValidationError` — bad tool arguments. Never calls BCCR.
- `UpstreamError(status, detail)` — BCCR returned a non-2xx.
- `AuthenticationError` — subclass of `UpstreamError` specific to 401s.

Plus a helper `to_mcp_tool_error(exc) -> str` that converts any of the above to a concise, token-free, user-facing message. The tool-wrapper decorator in `tools/exchange_rate.py` calls this helper.

## 4. `bccr/models.py` — BCCR payload shapes

The BCCR API speaks Spanish (`COD_INDICADORINTERNO`, `DES_FECHA`, `NUM_VALOR`). Three [`pydantic.BaseModel`s](02-python-concepts.md#4-pydantic-basemodel) model the relevant shapes:

- `RawSeriesPoint` — one BCCR row. Uses pydantic `Field(alias=...)` so we can carry Pythonic attribute names internally while accepting the Spanish originals from BCCR.
- `SeriesResponse` — envelope that wraps a list of rows. BCCR has shipped two variants historically (`Datos` and `Series`) so we accept either.
- `FlatRate` — our own internal row type. Exactly `date`, `buy`, `sell`. Has a `to_response_dict()` method that produces the MCP-facing shape (`YYYY-MM-DD` string, English keys).

## 5. `bccr/client.py` — the HTTP adapter

`BccrClient` is the only place in the codebase that knows BCCR URLs, header names, or Spanish date formatting.

Public surface:

- `await client.fetch_series(code, start, end)` — single series, raw points.
- `await client.fetch_buy_sell(start, end)` — merges series 317 + 318 into `FlatRate`s using `asyncio.gather` for a 2× latency win.

Error handling: HTTP 401 → `AuthenticationError`; any other non-2xx → `UpstreamError(status=…, detail=…)`; network/timeout → `UpstreamError(status=None, …)`. The bearer token is never logged or put in an exception message.

The class is an async context manager. Attempting to use it without `async with` raises a `RuntimeError` — that "fail fast" check is in `_require_http`.

## 6. `cache.py` — in-memory history

Two collaborating classes:

- `HistoricalRateCache` — a `dict[date, FlatRate]` with put/get/range helpers.
- `CacheBackedRateService` — the front door for the tool layer. `fetch_range(start, end)` splits the request at `today - 1`, pulls historical rows from the cache where possible, issues **one** BCCR call for the minimal contiguous missing sub-range, populates the cache, and fetches today live (never cached).

Today is determined via `today_in_costa_rica()`, which in turn reads `datetime.now(tz=ZoneInfo("America/Costa_Rica"))` [[`ZoneInfo`](02-python-concepts.md#9-zoneinfozoneinfo)]. Tests monkey-patch this single function to freeze the wall clock.

## 7. `tools/exchange_rate.py` — the MCP surface

Two tool functions live here. Both are decorated with our custom `@_translate_errors` [[decorator](02-python-concepts.md#7-decorators), [`@wraps`](02-python-concepts.md#8-functoolswraps)], which catches `BccrMcpError` subclasses and re-raises them as `ValueError` carrying a clean user-facing message. FastMCP surfaces the raised `ValueError` as an MCP tool error to the client.

### `get_current_exchange_rate(ctx)`

- Reads `today_in_costa_rica()`.
- Bypasses the cache entirely and calls `_client.fetch_buy_sell(today, today)` directly.
- Returns `{"date": "...", "buy": X, "sell": Y}`, or `{ ..., "message": "..." }` when BCCR has nothing published for today.

### `get_historical_exchange_rate(ctx, start_date, end_date, summarize=False, with_narrative=True)`

1. Parses dates (`_parse_iso_date`) — malformed input raises `ValidationError` before any I/O.
2. Validates range — `end < start` or `span > 3660` raises `ValidationError`.
3. Calls `_rate_service.fetch_range(start, end)` to get (cached + fresh) `FlatRate`s.
4. If `summarize=False`, renders each row via `.to_response_dict()` → `{"rates": [...]}`.
5. If `summarize=True`:
   - Calls `summarize_monthly(rates)` from `tools/summarize.py` → `list[dict]`.
   - If `months` is empty, returns `{"months": [], "message": "..."}`.
   - Otherwise, optionally attaches a `narrative` via MCP sampling (see next step).

### Sampling — `_client_supports_sampling` + `_request_narrative`

- `check_client_capability(ClientCapabilities(sampling=SamplingCapability()))` asks the session whether the connected client advertised `sampling` at handshake time. If it didn't, we return `False`; production callers interpret that as "skip the narrative".
- When sampling *is* supported, `build_sampling_request(months)` from `tools/narrative.py` constructs a compact `CreateMessageRequestParams`: a system prompt ("concise financial analyst"), a user message containing `months` as JSON plus a one-line directive, `max_tokens=200`, `temperature=0.3`, and `ModelPreferences(speed_priority=0.7, ...)`.
- The call is wrapped in `try/except Exception`: any failure (user rejection, timeout, transport error) logs a warning and returns `None`, so the tool still succeeds with just the structured months payload.

## 8. `tools/summarize.py` — the pure aggregator

A single function, `summarize_monthly(rates)`. It filters out rows whose `buy` *and* `sell` are both `None` (cached weekend/holiday markers), groups the remainder by `YYYY-MM` using [`collections.defaultdict`](02-python-concepts.md#13-collectionsdefaultdict), computes `min`/`max`/`mean` + `first`/`last` for each of `buy` and `sell`, and returns one dict per month sorted ascending.

Kept pure (no I/O, no MCP imports) so unit tests can be a handful of `_row(date(...), buy, sell)` calls.

## 9. `tools/narrative.py` — the sampling request builder

Another pure module. One function, `build_sampling_request(months)`, returns a `CreateMessageRequestParams` tailored for our narrative step. Keeping construction pure means the unit test is `build_sampling_request(fake_months); assert ...` — no FastMCP `Context` needed.

## 10. `server.py` — registering the tools

Tiny file. Creates the FastMCP instance (`FastMCP("bccr")`) and registers our two tool functions. The reason we register here rather than decorating the functions in `tools/exchange_rate.py` directly is so that all registrations live in one place.

---

## Putting it together: a tool call end-to-end

Say the user asks Claude Desktop for "average USD rate per month over the last 3 years with a narrative."

1. **Claude Desktop** decides to call `get_historical_exchange_rate(start_date="2023-04-22", end_date="2026-04-22", summarize=True)` — `with_narrative` defaults to `True`.
2. **Transport**: a JSON-RPC line lands on the server's stdin.
3. **FastMCP** dispatches it to the registered tool. Our `@_translate_errors` decorator activates first.
4. **Validation**: `_parse_iso_date` and `_validate_range` pass.
5. **Rate service**: `CacheBackedRateService.fetch_range` splits the request at `today - 1`. First call of the session — cache is empty — so it issues one `BccrClient.fetch_buy_sell(...)` call covering everything *up to* yesterday, and a second call for today.
6. **BCCR client** fires series 317 + 318 concurrently via `asyncio.gather`, merges to `FlatRate`s, returns.
7. **Cache** stores every yesterday-and-earlier row for next time.
8. **Summarize**: `summarize_monthly(rates)` collapses ~750 daily rows into ~36 monthly buckets.
9. **Capability check**: `check_client_capability(sampling=...)` returns `True` (Claude Desktop supports sampling).
10. **Sampling**: we call `ctx.session.create_message(...)` with the constructed params. Claude Desktop shows a consent prompt; the user approves; the client returns a one-sentence narrative.
11. **Assembly**: `{"months": [...], "narrative": "..."}` goes back down the JSON-RPC wire.
12. **Claude Desktop** renders the narrative + optionally uses the structured data for further reasoning or charts.

That's the whole loop — one user question, one round of MCP protocol, one optional sampling round trip, a concise structured answer.
