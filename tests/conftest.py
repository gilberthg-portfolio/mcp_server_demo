"""Shared pytest fixtures.

This module is auto-discovered by pytest — every fixture defined here is
available to every test file without an explicit import.

We expose a single fixture (``mock_bccr``) that installs ``respx`` as an
httpx interceptor, so tests never touch the real BCCR API.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx

# --- Python idiom: `pytest` fixtures ----------------------------------------
# A function decorated with ``@pytest.fixture`` is a reusable piece of setup.
# Test functions that declare a parameter with the fixture's name receive
# the fixture's return value. ``yield`` inside a fixture separates setup
# from teardown — anything after ``yield`` runs when the test ends.


@pytest.fixture
def mock_bccr() -> Iterator[respx.MockRouter]:
    """Return a ``respx`` router that intercepts every outbound httpx call.

    Tests use this to assert which URL was hit and to return canned responses.
    ``assert_all_called=False`` keeps the router from failing a test that
    happens not to exercise every registered route — useful for sharing one
    fixture across many cases.
    """
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router
