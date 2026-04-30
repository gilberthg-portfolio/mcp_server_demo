"""Environment-driven configuration for the BCCR MCP server.

This module is the single place that reads environment variables. Everywhere
else in the codebase receives a fully-populated ``Settings`` object, which
means the rest of the server does not care *where* the token came from.

Only one real secret is consumed: ``BCCR_TOKEN`` (the bearer token supplied to
every BCCR request). Username / password are *never* read — the server does
not implement the "username+password → bearer" exchange flow.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# --- Python idiom: third-party library import -------------------------------
# `python-dotenv` reads key=value lines from a `.env` file and pushes them into
# `os.environ`. It is optional — if no `.env` exists, we fall back to whatever
# is already in the environment (e.g. values injected by Claude Desktop via
# `mcpServers.env`). We import its callable lazily inside ``load_settings`` so
# that unit tests can patch the environment without worrying about import-time
# side effects.

from .errors import ConfigurationError


# Default BCCR API base URL. Exposed as a module-level constant so tests can
# override it via the ``BCCR_BASE_URL`` env var without monkeypatching.
BCCR_BASE_URL_DEFAULT = (
    "https://apim.bccr.fi.cr/SDDE/api/Bccr.GE.SDDE.Publico.Indicadores.API"
)


# --- Python idiom: @dataclass ----------------------------------------------
# `@dataclass(frozen=True)` auto-generates __init__, __repr__, and __eq__ from
# the annotated fields and makes the resulting instance immutable (setting an
# attribute after construction raises FrozenInstanceError). For a small bag of
# config values this gives us a clean read-only record with no boilerplate.
@dataclass(frozen=True)
class Settings:
    """Validated configuration used by the server at runtime."""

    bccr_token: str
    bccr_base_url: str


def load_settings() -> Settings:
    """Read environment variables and return a validated ``Settings``.

    Raises ``ConfigurationError`` if ``BCCR_TOKEN`` is missing or empty.

    Side effects:
        * If a ``.env`` file exists in the current working directory it is
          loaded into ``os.environ`` *before* we read any variables.
        * No other files are touched.
    """
    # Late import keeps python-dotenv optional at import time and makes the
    # module cheaper to re-import during tests.
    from dotenv import load_dotenv

    # We pin the lookup to the *current working directory's* .env rather than
    # using ``load_dotenv()``'s default "walk up the call-stack file's parents"
    # behavior. Two reasons:
    #   1. It matches the documented behavior (this docstring promised cwd).
    #   2. It lets tests use ``monkeypatch.chdir(tmp_path)`` to isolate from
    #      the developer's real .env. With the magic-find default, a stray
    #      .env anywhere up the tree leaks into tests.
    # `override=False` means an already-set env var beats a .env value; this
    # is the behavior Claude Desktop's injected `env` block relies on.
    env_file = Path(".env")
    if env_file.is_file():
        load_dotenv(dotenv_path=env_file, override=False)

    # .strip() guards against invisible whitespace in copy/pasted tokens.
    token = (os.environ.get("BCCR_TOKEN") or "").strip()
    if not token:
        raise ConfigurationError(
            "BCCR_TOKEN is not set. Add it to your environment or to a .env file "
            "at the project root. See .env.example for the expected format."
        )

    base_url = (os.environ.get("BCCR_BASE_URL") or BCCR_BASE_URL_DEFAULT).strip()

    return Settings(bccr_token=token, bccr_base_url=base_url)
