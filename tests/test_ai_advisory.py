"""
tests/test_ai_advisory.py

Unit tests for the Amazon Bedrock tactical safety briefing service.
Verifies parsing of Claude 3 Haiku responses, in-memory cache deduplication,
and resilient static fallback on Bedrock API errors.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from services import ai_advisory
from services.ai_advisory import _ADVISORY_CACHE, get_safety_briefing


@pytest.fixture(autouse=True)
def _clear_advisory_cache() -> Iterator[None]:
    """Clear the in-memory cache before and after every test."""
    _ADVISORY_CACHE.clear()
    yield
    _ADVISORY_CACHE.clear()


def _mock_bedrock_response(text: str) -> dict[str, Any]:
    """Construct a mock Bedrock invoke_model response structure."""
    payload = json.dumps({"content": [{"text": text}]}).encode("utf-8")
    return {"body": io.BytesIO(payload)}


class TestAiAdvisoryService:
    @pytest.mark.asyncio
    async def test_parses_three_bullet_points_correctly(self) -> None:
        mock_raw_output = (
            "- Hydrate with at least 0.75 L of electrolyte water per hour.\n"
            "- Rotate into air-conditioned cooling stations every 30 minutes.\n"
            "- Monitor coworkers for early symptoms of dizziness or heat cramps."
        )

        mock_client = MagicMock()
        mock_client.invoke_model.return_value = _mock_bedrock_response(mock_raw_output)

        with patch("boto3.client", return_value=mock_client):
            bullets = await get_safety_briefing("Extreme Caution", "outdoor_worker", "en")

        assert len(bullets) == 3
        assert bullets[0] == "Hydrate with at least 0.75 L of electrolyte water per hour."
        assert bullets[1] == "Rotate into air-conditioned cooling stations every 30 minutes."
        assert bullets[2] == "Monitor coworkers for early symptoms of dizziness or heat cramps."
        assert mock_client.invoke_model.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_hit_prevents_subsequent_bedrock_invocations(self) -> None:
        mock_raw_output = (
            "- Take frequent rest breaks in shaded areas.\n"
            "- Keep cold water bottles immediately on hand.\n"
            "- Watch for signs of heavy sweating or fatigue."
        )

        mock_client = MagicMock()
        mock_client.invoke_model.return_value = _mock_bedrock_response(mock_raw_output)

        with patch("boto3.client", return_value=mock_client):
            # First invocation: uncached, should call Bedrock
            first_result = await get_safety_briefing("Caution", "general_public", "en")
            # Second invocation with identical parameters: cached, should not call Bedrock
            second_result = await get_safety_briefing("Caution", "general_public", "en")

        assert first_result == second_result
        assert mock_client.invoke_model.call_count == 1
        assert ("Caution", "general_public", "en") in _ADVISORY_CACHE

    @pytest.mark.asyncio
    async def test_exception_triggers_fallback_dictionary_seamlessly(self) -> None:
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate limit exceeded"}},
            "InvokeModel",
        )

        with patch("boto3.client", return_value=mock_client):
            bullets = await get_safety_briefing("Danger", "outdoor_worker", "en")

        assert len(bullets) == 3
        for bullet in bullets:
            assert isinstance(bullet, str)
            assert len(bullet) > 0
        assert "Drink one liter of cool electrolyte water every hour." in bullets[0]
        # Should also be cached so next retrieval is instant
        assert ("Danger", "outdoor_worker", "en") in _ADVISORY_CACHE

    @pytest.mark.asyncio
    @pytest.mark.parametrize("lang", ["ar", "hi", "es"])
    async def test_multilingual_fallback_support(self, lang: str) -> None:
        mock_client = MagicMock()
        mock_client.invoke_model.side_effect = RuntimeError("AWS credentials not configured")

        with patch("boto3.client", return_value=mock_client):
            bullets = await get_safety_briefing("Extreme Danger", "general_public", lang)

        assert len(bullets) == 3
        for bullet in bullets:
            assert isinstance(bullet, str)
            assert len(bullet) > 0
