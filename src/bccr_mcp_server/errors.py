"""Custom exception hierarchy for the BCCR MCP server.

Why a hierarchy?
----------------
Tool handlers need to translate internal failures into user-facing error
messages. Having a small, well-typed set of exceptions lets the tool wrapper
do that mapping with a clean `isinstance` / match, instead of sprinkling
``if isinstance(...)`` chains across every call site.

Categories:
    * ConfigurationError   -- env vars missing or malformed (raised at startup).
    * ValidationError      -- bad tool arguments (dates, range). Never calls BCCR.
    * UpstreamError        -- BCCR returned a non-2xx (or we couldn't reach it).
    * AuthenticationError  -- subclass of UpstreamError for 401s specifically.

A single ``to_mcp_tool_error(exc)`` helper converts any of the above into the
human-readable string we hand back to the MCP client. The helper also makes
sure we never echo the bearer token in an error message.
"""

from __future__ import annotations

# --- Python idiom: `from __future__ import annotations` ---------------------
# This line defers evaluation of every type annotation in this module to a
# string form. Two benefits:
#   1. Forward references work without quotes (e.g. you can write
#      `def f(x: "MyClass") -> None` as `def f(x: MyClass) -> None`).
#   2. Annotations never run at import time, so circular-import risks shrink.
# We enable it at the top of every module in this project.


class BccrMcpError(Exception):
    """Base class for every exception the server raises intentionally.

    All server-side errors inherit from this one so that the tool wrapper can
    catch ``BccrMcpError`` once and translate the result. Anything that isn't
    a ``BccrMcpError`` is treated as a bug and propagates.
    """


class ConfigurationError(BccrMcpError):
    """Raised when environment configuration is missing or invalid.

    Only raised at startup (in ``config.load_settings``). After the server is
    running this class should never be instantiated — config is frozen by then.
    """


class ValidationError(BccrMcpError):
    """Raised when the *caller* of a tool supplied bad arguments.

    Examples: malformed date string, ``end_date < start_date``, date range
    wider than the configured cap. These errors are returned to the client
    verbatim — they describe a fixable mistake on the caller's side.
    """


class UpstreamError(BccrMcpError):
    """Raised when the BCCR API returns a non-2xx response or is unreachable.

    ``status`` is the HTTP status code (or ``None`` for transport/timeout
    errors), and ``detail`` is the BCCR-supplied error description when we
    could parse one.
    """

    def __init__(self, status: int | None, detail: str | None = None) -> None:
        # We build the human-readable message here so the base Exception
        # class has something sensible to show. Downstream code should
        # prefer ``to_mcp_tool_error`` rather than stringifying directly.
        message = f"BCCR responded with status {status}"
        if detail:
            message += f": {detail}"
        super().__init__(message)
        self.status = status
        self.detail = detail


class AuthenticationError(UpstreamError):
    """Specific BCCR-401 flavor of UpstreamError.

    Carved out as its own class so the tool wrapper can tell the user to
    regenerate their bearer token (rather than silently retrying).
    """

    def __init__(self, detail: str | None = None) -> None:
        # Status is always 401 for this subclass — passed explicitly so the
        # base ``UpstreamError`` message stays consistent.
        super().__init__(status=401, detail=detail)


# ---------------------------------------------------------------------------
# Mapping to user-facing messages
# ---------------------------------------------------------------------------

# Each branch below produces a short, actionable string. We deliberately avoid
# leaking the bearer token, full URLs with query strings, or raw tracebacks.

_EXPIRED_TOKEN_MSG = (
    "Your BCCR bearer token appears to be invalid or expired. "
    "Generate a new one from the BCCR developer portal and update the "
    "BCCR_TOKEN environment variable."
)

_UPSTREAM_UNAVAILABLE_MSG = (
    "The BCCR service is temporarily unavailable. Please try again in a few "
    "minutes."
)


def to_mcp_tool_error(exc: BaseException) -> str:
    """Convert any exception into the user-facing MCP tool-error text.

    The caller (tool wrapper in ``tools/exchange_rate.py``) is responsible for
    re-raising as an MCP ``ToolError`` — this helper just builds the string.
    """
    # 401 → specific "regenerate the token" wording.
    if isinstance(exc, AuthenticationError):
        return _EXPIRED_TOKEN_MSG

    # Other upstream failures branch on status code.
    if isinstance(exc, UpstreamError):
        # No status usually means "network unreachable / timeout".
        if exc.status is None or 500 <= exc.status <= 599:
            return _UPSTREAM_UNAVAILABLE_MSG
        # Non-401 4xx — BCCR told us what's wrong; relay the gist.
        if exc.detail:
            return f"BCCR rejected the request: {exc.detail}"
        return f"BCCR rejected the request (HTTP {exc.status})."

    # Bad user input.
    if isinstance(exc, ValidationError):
        return str(exc)

    # Startup configuration problems should never reach this helper because
    # the server exits before tool handlers can be called — but we handle it
    # just in case, rather than leak the raw exception text.
    if isinstance(exc, ConfigurationError):
        return "Server configuration is incomplete. See server logs."

    # Unknown exception — do not leak the repr (could contain the token in
    # some theoretical future code path). Generic message only.
    return "An unexpected error occurred while processing the request."
