"""Tests for tp_get_peak_trends (fitnesshistory report)."""

from unittest.mock import AsyncMock, patch

import pytest

from tp_mcp.client.http import APIResponse, ErrorCode
from tp_mcp.tools.peak_trends import tp_get_peak_trends

ENTRY = {
    "startDate": "2026-09-28T00:00:00",
    "endDate": "2026-10-04T00:00:00",
    "totalDistance": 48996.5,
    "totalDuration": 2.4586,
    "totalTSS": 118.57,
    "totalKJ": 1191.261,
    "includeMaxType": 4,
    "peak01": 497.0, "peak01WorkoutIds": [11, 12],
    "peak02": 301.0, "peak02WorkoutIds": [11],
    "peak03": 271.0, "peak03WorkoutIds": [13],
    "peak04": 251.0, "peak04WorkoutIds": [13],
    "peak05": None, "peak05WorkoutIds": [],
}


async def _call(response, **kwargs):
    client = AsyncMock()
    client.ensure_athlete_id = AsyncMock(return_value=123)
    client.post = AsyncMock(return_value=response)
    with patch("tp_mcp.tools.peak_trends.TPClient") as mtp:
        mtp.return_value.__aenter__.return_value = client
        result = await tp_get_peak_trends("2026-09-28", "2026-10-04", **kwargs)
    return result, client


class TestPeakTrends:
    @pytest.mark.asyncio
    async def test_maps_periods_and_peaks(self):
        result, client = await _call(APIResponse(success=True, data=[ENTRY]))
        assert client.post.await_args.args[0].endswith("/reporting/fitnesshistory/2026-09-28/2026-10-04")
        assert client.post.await_args.kwargs["json"] == {"group": 0, "peakType": 4, "workoutTypeIds": []}
        period = result["periods"][0]
        assert period["distance_km"] == 49.0 and period["duration_hours"] == 2.46
        assert [p["duration"] for p in period["peaks"]] == ["5 s", "1 min", "5 min", "20 min"]
        assert period["peaks"][0] == {"duration": "5 s", "value": 497.0, "workout_id": "11"}
        assert period["tss"] == 118.6 and period["energy_kj"] == 1191
        assert result["metric"] == "power" and result["count"] == 1

    @pytest.mark.asyncio
    async def test_month_heart_rate_with_sport_filter(self):
        _, client = await _call(
            APIResponse(success=True, data=[]), metric="heart_rate", group_by="month", workout_type_ids=[2]
        )
        assert client.post.await_args.kwargs["json"] == {"group": 1, "peakType": 1, "workoutTypeIds": ["2"]}

    @pytest.mark.asyncio
    async def test_pace_by_distance_uses_distance_labels(self):
        result, client = await _call(APIResponse(success=True, data=[ENTRY]), metric="pace_by_distance")
        assert client.post.await_args.kwargs["json"]["peakType"] == 6
        assert [p["duration"] for p in result["periods"][0]["peaks"]] == ["400 m", "1 km", "1 mile", "5 km"]

    @pytest.mark.asyncio
    async def test_api_error_and_validation(self):
        bad = APIResponse(success=False, error_code=ErrorCode.API_ERROR, message="boom")
        assert (await _call(bad))[0]["error_code"] == "API_ERROR"
        assert (await _call(bad, metric="vo2"))[0]["error_code"] == "VALIDATION_ERROR"
        assert (await _call(bad, group_by="day"))[0]["error_code"] == "VALIDATION_ERROR"
        with patch("tp_mcp.tools.peak_trends.TPClient"):
            r = await tp_get_peak_trends("2020-01-01", "2026-10-01")
        assert r["error_code"] == "VALIDATION_ERROR"
