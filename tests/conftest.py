"""
conftest.py

Shared pytest fixtures for the HeatSafe test suite.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Restrict anyio-based async tests to the asyncio backend (no trio dependency)."""
    return "asyncio"
