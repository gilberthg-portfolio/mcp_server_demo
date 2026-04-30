# 02 — Python concepts used in this codebase

This file is a **glossary**. Each section is a short, standalone explanation of one Python idiom and a pointer to where we first use it. Skim the headings, jump to whichever one you need.

The sections, in rough order of appearance:

1. [`from __future__ import annotations`](#1-from-__future__-import-annotations)
2. [Type hints & `typing`](#2-type-hints--typing)
3. [Dataclasses — `@dataclass(frozen=True)`](#3-dataclasses--dataclassfrozentrue)
4. [Pydantic `BaseModel`](#4-pydantic-basemodel)
5. [`async` / `await`](#5-async--await)
6. [Async context managers — `async with`, `__aenter__` / `__aexit__`](#6-async-context-managers--async-with-__aenter__--__aexit__)
7. [Decorators](#7-decorators)
8. [`functools.wraps`](#8-functoolswraps)
9. [`zoneinfo.ZoneInfo`](#9-zoneinfozoneinfo)
10. [`httpx.AsyncClient`](#10-httpxasyncclient)
11. [The `src/` layout and `__main__`](#11-the-src-layout-and-__main__)
12. [Module-level singletons + closure DI](#12-module-level-singletons--closure-di)
13. [`collections.defaultdict`](#13-collectionsdefaultdict)
14. [`pytest` fixtures & `monkeypatch`](#14-pytest-fixtures--monkeypatch)
15. [`respx` — mocking httpx](#15-respx--mocking-httpx)

---

## 1. `from __future__ import annotations`

Every module in this project starts with this line. It tells Python **not** to evaluate annotations at import time — they stay as strings. Two wins: forward references just work (no `"MyClass"` quoting), and it removes a category of circular-import bugs.

First appearance: `src/bccr_mcp_server/errors.py`.

## 2. Type hints & `typing`

Modern Python type hints describe what a function expects and returns:

```python
def add(a: int, b: int) -> int: ...
```

Runtime behavior is unchanged — hints are for humans and tools. We use them everywhere; pydantic and FastMCP both read them to auto-generate schemas.

Things to know:
- `X | Y` (Python 3.10+) is the same as `Union[X, Y]`.
- `list[int]`, `dict[str, int]` (Python 3.9+) work without importing from `typing`.
- `Self` (Python 3.11+) means "the same class I'm in".

First appearance: everywhere. A good example is `BccrClient.__aenter__` in `src/bccr_mcp_server/bccr/client.py`.

## 3. Dataclasses — `@dataclass(frozen=True)`

A `@dataclass` auto-generates `__init__`, `__repr__`, and `__eq__` from the class's annotated fields:

```python
@dataclass(frozen=True)
class Settings:
    bccr_token: str
    bccr_base_url: str
```

`frozen=True` makes instances immutable — assigning to a field after construction raises. Great for config.

First appearance: `src/bccr_mcp_server/config.py`.

## 4. Pydantic `BaseModel`

Pydantic is a validation library. A class that inherits from `pydantic.BaseModel`:

- Validates and coerces incoming data at construction.
- Serializes to/from JSON with `model_validate` / `model_dump`.
- Supports field-level aliases (useful when the incoming API uses different names than what you want in Python).

We use it to parse BCCR's JSON (Spanish field names) into Python-friendly objects.

First appearance: `src/bccr_mcp_server/bccr/models.py`. Look for `RawSeriesPoint`.

## 5. `async` / `await`

Python has native support for cooperative concurrency via the `async`/`await` syntax.

- A function declared `async def` returns a *coroutine* when called — it hasn't run yet.
- You `await` a coroutine to actually run it and get its result.
- Multiple coroutines can be awaited concurrently via `asyncio.gather(...)`.

Short example:

```python
async def hello() -> str:
    return "hi"

async def main() -> None:
    text = await hello()   # text == "hi"
```

In our codebase, the two BCCR series calls run concurrently:

```python
buy_points, sell_points = await asyncio.gather(
    self.fetch_series(BCCR_INDICATOR_BUY, start, end),
    self.fetch_series(BCCR_INDICATOR_SELL, start, end),
)
```

That line cuts wall-clock latency roughly in half compared to sequential awaits.

First appearance: `src/bccr_mcp_server/bccr/client.py::BccrClient.fetch_series`.

## 6. Async context managers — `async with`, `__aenter__` / `__aexit__`

You've probably seen the sync version:

```python
with open("file.txt") as f:
    ...
```

`async with` is the same pattern for async resources:

```python
async with BccrClient(...) as client:
    rates = await client.fetch_buy_sell(...)
```

A class becomes usable this way when it defines `async def __aenter__` (setup) and `async def __aexit__` (teardown). Enter returns the bound value (`client` in the snippet above); exit is called even if an exception is raised inside the block.

First appearance: `src/bccr_mcp_server/bccr/client.py`.

## 7. Decorators

A decorator is a function that takes a function and returns a (usually wrapped) function. The `@name` syntax is just sugar:

```python
@my_decorator
def f():
    ...

# equivalent to:

def f():
    ...
f = my_decorator(f)
```

We use decorators for:

- **`@mcp.tool()`** — registers a tool with FastMCP (see `server.py`).
- **`@dataclass`** — auto-generates `__init__` etc. (see `config.py`).
- **`@_translate_errors`** — our own wrapper that catches internal exceptions (see `tools/exchange_rate.py`).
- **`@pytest.fixture`** — registers reusable setup for tests (see `tests/conftest.py`).

First appearance: `src/bccr_mcp_server/config.py::Settings` (as `@dataclass`). Our own decorator lives in `tools/exchange_rate.py::_translate_errors`.

## 8. `functools.wraps`

When you write a custom decorator, the returned function loses the original's `__name__`, `__doc__`, etc. That breaks tools that read those attributes — including FastMCP.

```python
from functools import wraps

def log_calls(fn):
    @wraps(fn)               # preserves name and docstring
    def wrapper(*a, **kw):
        print("calling", fn.__name__)
        return fn(*a, **kw)
    return wrapper
```

First appearance: `src/bccr_mcp_server/tools/exchange_rate.py::_translate_errors`.

## 9. `zoneinfo.ZoneInfo`

Standard-library timezone support since Python 3.9:

```python
from zoneinfo import ZoneInfo
tz = ZoneInfo("America/Costa_Rica")
```

Used to compute "today in Costa Rica" regardless of where the server runs.

First appearance: `src/bccr_mcp_server/cache.py::COSTA_RICA_TZ`.

## 10. `httpx.AsyncClient`

Modern async HTTP client, structurally similar to `requests` but async-native. Key methods: `client.get(url, params=..., headers=...)`, `response.status_code`, `response.json()`, `response.text`.

Reuse of a single `AsyncClient` across calls is important — it holds a connection pool. That's why we keep ours alive for the lifetime of the server via the async context manager.

First appearance: `src/bccr_mcp_server/bccr/client.py`.

## 11. The `src/` layout and `__main__`

Our importable code lives under `src/bccr_mcp_server/`, not at the repo root. Two benefits:

1. Tests can't accidentally import from the source tree instead of the installed package.
2. `pip install -e .` is the canonical way to run locally — no `PYTHONPATH` gymnastics.

The `__main__.py` file inside the package is what runs when you do `python -m bccr_mcp_server`. It contains the classic guard:

```python
if __name__ == "__main__":
    main()
```

That guard means the same file can be imported by tests *without* running `main()`.

First appearance: `src/bccr_mcp_server/__main__.py`.

## 12. Module-level singletons + closure DI

"Dependency injection" has a fancy reputation but at its simplest: pass collaborators to the code that needs them instead of constructing them inside. In MCP tool functions we can't easily add extra parameters (FastMCP inspects the signature to build the schema), so we use **module-level slots**:

```python
_client: BccrClient | None = None

def configure_tools(client: BccrClient) -> None:
    global _client
    _client = client
```

Then tool bodies reach into `_client`. The server's `__main__.main()` populates the slot before the event loop starts.

First appearance: `src/bccr_mcp_server/tools/exchange_rate.py::configure_tools`.

## 13. `collections.defaultdict`

A dict that creates a default value (via a factory) the first time a key is accessed:

```python
from collections import defaultdict
groups = defaultdict(list)
for row in rows:
    groups[row.category].append(row)       # no KeyError on first touch
```

First appearance: `src/bccr_mcp_server/tools/summarize.py::summarize_monthly`.

## 14. `pytest` fixtures & `monkeypatch`

A `@pytest.fixture` is reusable test setup. Tests receive it by declaring a parameter with the fixture's name. Fixtures can `yield` to separate setup from teardown.

`monkeypatch` is a built-in fixture for patching the environment or module attributes *for the duration of one test*:

```python
def test_reads_env(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    # …
```

First appearance: `tests/conftest.py::mock_bccr`, and any `test_*.py` that uses `monkeypatch`.

## 15. `respx` — mocking httpx

`respx` intercepts outbound httpx calls so tests never hit the real network:

```python
with respx.mock() as router:
    router.get("https://api.example.com/x").mock(return_value=httpx.Response(200, json={"ok": True}))
    # httpx.get(...) from production code now returns the stubbed response
```

We wrap it in a `conftest.py` fixture (`mock_bccr`) so every test can reach for it with zero ceremony.

First appearance: `tests/conftest.py::mock_bccr`.
