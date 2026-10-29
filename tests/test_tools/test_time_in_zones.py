"""Tests for tp_get_time_in_zones.

The response fixtures are built from the key names recorded from the web app's
dashboard (zone1Minutes..zone10Minutes); the values are synthetic.
"""

from unittest.mock import AsyncMock, patch

import pytest

from tp_mcp.client.http import APIResponse
from tp_mcp.tools.time_in_zones import tp_get_time_in_zones

ENTRY = {
    "startDate": "2026-01-05T00:00:00",
    "endDate": "2026-01-11T00:00:00",
    **{f"zone{i}Minutes": 0 for i in range(1, 11)},
    "zone1Minutes": 30.0,
    "zone2Minutes": 120.5,
    "zone4Minutes": 10.0,
}


def _client(data):
    instance = AsyncMock()
    instance.ensure_athlete_id = AsyncMock(return_value=123)
    instance.post = AsyncMock(return_value=APIResponse(success=True, data=data))
    return instance


async def _call(data, **kwargs):
    instance = _client(data)
    with patch("tp_mcp.tools.time_in_zones.TPClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = instance
        result = await tp_get_time_in_zones("2026-01-05", "2026-01-11", **kwargs)
    return result, instance


class TestTimeInZones:
    @pytest.mark.asyncio
    async def test_total_trims_unused_high_zones(self):
        result, instance = await _call(ENTRY)
        assert result["zone_type"] == "heart_rate"
        assert [z["zone"] for z in result["zones"]] == [1, 2, 3, 4]
        assert result["zones"][1]["minutes"] == 120.5
        assert result["total_minutes"] == 160.5
        endpoint = instance.post.await_args.args[0]
        assert endpoint == "/fitness/v1/athletes/123/reporting/timeinzones/2026-01-05/2026-01-11"
        assert instance.post.await_args.kwargs["json"] == {"workoutTypeIds": [], "timeInZonesType": 1}

    @pytest.mark.asyncio
    async def test_by_week_uses_weekly_report(self):
        result, instance = await _call([ENTRY, ENTRY], zone_type="power", by_week=True, workout_type_ids=[2])
        assert result["count"] == 2
        assert result["weeks"][0]["start_date"] == "2026-01-05"
        assert "timeinzonesbyweek" in instance.post.await_args.args[0]
        assert instance.post.await_args.kwargs["json"] == {"workoutTypeIds": ["2"], "timeInZonesType": 2}

    @pytest.mark.asyncio
    async def test_empty_response(self):
        result, _ = await _call({})
        assert result["zones"] == [] and result["total_minutes"] == 0

    @pytest.mark.asyncio
    async def test_invalid_zone_type(self):
        result, instance = await _call(ENTRY, zone_type="cadence")
        assert result["error_code"] == "VALIDATION_ERROR"
        instance.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_range_too_large_and_reversed(self):
        with patch("tp_mcp.tools.time_in_zones.TPClient"):
            big = await tp_get_time_in_zones("2025-01-01", "2026-06-01")
            rev = await tp_get_time_in_zones("2026-02-01", "2026-01-01")
        assert big["error_code"] == "VALIDATION_ERROR"
        assert rev["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_api_error_passthrough(self):
        from tp_mcp.client.http import ErrorCode

        instance = AsyncMock()
        instance.ensure_athlete_id = AsyncMock(return_value=123)
        instance.post = AsyncMock(
            return_value=APIResponse(success=False, error_code=ErrorCode.API_ERROR, message="boom")
        )
        with patch("tp_mcp.tools.time_in_zones.TPClient") as mock_client:
            mock_client.return_value.__aenter__.return_value = instance
            result = await tp_get_time_in_zones("2026-01-05", "2026-01-11")
        assert result["isError"] and result["message"] == "boom"
