# 01 — The Model Context Protocol, in two pages

## What MCP is

The **Model Context Protocol (MCP)** is a small JSON-RPC-based protocol for letting a language model talk to external services. It was introduced by Anthropic in late 2024 and is supported by Claude Desktop, Cursor, Continue, and a growing list of IDE plugins.

The core idea: instead of every AI application hardcoding its own "tools" (search my email, query my database, call my API), you run a **server** that exposes those tools once, and any MCP-compatible **client** can use them.

## Vocabulary

| Term | What it means | Example in *this* project |
|------|---------------|---------------------------|
| **Host** | The app the user opens (e.g. Claude Desktop). | Claude Desktop. |
| **Client** | A thin layer inside the host that speaks MCP. One per server connection. | The MCP client inside Claude Desktop. |
| **Server** | A program that advertises tools/resources/prompts. Can be local (stdio) or remote (SSE/WebSocket). | `bccr-mcp-server`. |
| **Tool** | A function the model can call. Has a name, a description, and a JSON schema for its arguments. | `get_current_exchange_rate`, `get_historical_exchange_rate`. |
| **Resource** | Data the model can read (file, URL, etc). We don't use any. | — |
| **Prompt** | A server-defined prompt template. We don't use any. | — |
| **Transport** | How client and server talk. Stdio for local, SSE/WS for remote. | Stdio — the server is spawned as a subprocess by Claude Desktop. |

## How a tool call works

```
    Claude Desktop (host + client)             Your server
    ─────────────────────────────────          ─────────────────────
    (1) user: "what's the dollar rate today?"
                                          →    initialize / list tools
    (2) model picks get_current_exchange_rate
    (3) client sends: tools/call                →
                                                 run the tool function
                                          ←      return the JSON result
    (4) model reads the result, answers user
```

Every tool call is a single JSON-RPC round trip over stdin/stdout (in our case).

## Capability negotiation

When the client and server connect, each advertises which **capabilities** it supports. The server says "I have tools"; the client may say "I support sampling" (more on that below). Either side can check what the other supports *before* trying to use a feature.

In our codebase that check lives in `_client_supports_sampling` inside `tools/exchange_rate.py` — we only try to produce a narrative if the client told us it supports sampling.

## What is sampling?

Normally it's *the server* that does all the work. **Sampling** flips that: the server asks the client ("please have your LLM generate some text for me"), the client routes the prompt through the user's configured model, and returns the result. It's how a server can leverage the host's LLM without shipping its own API key.

The flow:

```
    Server (tool running)           Client (Claude Desktop)        Model
    ─────────────────────           ───────────────────────        ─────
    sampling/createMessage  →       (asks user to approve)
                                    (forwards to model)        →
                                                              ←   completion
                                ←   { content: "..." }
    attach to tool response
```

In our project we use this in exactly one place: when the user calls `get_historical_exchange_rate(summarize=True)` and hasn't set `with_narrative=False`, we ask the model to write a one-to-two-sentence narrative of the trend. See `tools/exchange_rate.py::_request_narrative` and `tools/narrative.py::build_sampling_request`.

Two important properties we rely on:

1. **Consent-first.** Clients typically surface a "server X wants to send a prompt to your model" dialog. The user can reject the request.
2. **Optional capability.** Not every client implements sampling. We capability-check first and gracefully omit the narrative field if the client says no.

## Transport: stdio

Our server is a *local* MCP server. Claude Desktop spawns it as a subprocess when it first connects, feeds JSON-RPC on its `stdin`, and reads replies from `stdout`. That has one important consequence: **never `print()` anything to stdout from a local MCP server**, or you will corrupt the protocol stream. Use `stderr` for logging — that's what `__main__._configure_logging` sets up.

## Where to go next

- **Want to see the code patterns?** → [`02-python-concepts.md`](02-python-concepts.md)
- **Want the full guided tour?** → [`03-code-walkthrough.md`](03-code-walkthrough.md)
