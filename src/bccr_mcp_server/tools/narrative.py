"""Helpers for the optional MCP-sampling narrative on summarized responses.

When a caller opts in (``with_narrative=True`` on ``get_historical_exchange_rate``)
and the connected client supports sampling, we ask the client's LLM to write
a one-to-two-sentence description of the monthly-bucket data. This module's
job is limited to *building* the sampling request; the actual dispatch
(``ctx.session.create_message(...)``) happens in ``tools/exchange_rate.py``.

Why separate? Keeping the prompt construction pure makes it trivial to unit
test without faking an MCP ``Context``.
"""

from __future__ import annotations

import json
from typing import Any

# --- MCP types --------------------------------------------------------------
# These types describe the parameters of a ``sampling/createMessage`` request.
# The actual call is made via the server's ``Context.session.create_message``
# method; we just construct the inputs.
from mcp.types import (
    CreateMessageRequestParams,
    ModelHint,
    ModelPreferences,
    SamplingMessage,
    TextContent,
)


SYSTEM_PROMPT = (
    "You are a concise financial analyst. You describe USD/CRC exchange-rate "
    "trends in one or two sentences, grounded strictly in the data you are "
    "given. Never invent numbers. Never speculate beyond the provided range."
)

USER_DIRECTIVE = (
    "In one or two sentences, describe the overall USD/CRC trend for the "
    "months below, plus any month with unusual volatility. Be specific about "
    "the direction (strengthening / weakening CRC) but avoid jargon."
)


def build_sampling_request(
    months: list[dict[str, Any]],
) -> CreateMessageRequestParams:
    """Return a ready-to-send ``CreateMessageRequestParams``.

    The caller passes this to ``ctx.session.create_message(**params.model_dump())``
    (or a similar helper exposed by the FastMCP SDK version in use).
    """
    # The user message is plain text: a one-line directive followed by the
    # monthly buckets as JSON. Keeping it short controls prompt-token cost
    # and makes the "concise" instruction easy for any model to follow.
    user_text = f"{USER_DIRECTIVE}\n\n{json.dumps(months, ensure_ascii=False, indent=2)}"

    return CreateMessageRequestParams(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(type="text", text=user_text),
            )
        ],
        systemPrompt=SYSTEM_PROMPT,
        maxTokens=200,
        temperature=0.3,
        modelPreferences=ModelPreferences(
            # Hints guide the client toward a small/fast model without
            # naming one. Values are 0..1; higher = more of that attribute.
            hints=[ModelHint(name="haiku")],
            speedPriority=0.7,
            intelligencePriority=0.3,
            costPriority=0.5,
        ),
    )
