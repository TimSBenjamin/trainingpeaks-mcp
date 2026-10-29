"""Tests for tp_get_training_summary (workoutsummary / fitnesssummary reports)."""

from unittest.mock import AsyncMock, patch

import pytest

from tp_mcp.client.http import APIResponse, ErrorCode
from tp_mcp.tools.training_summary import tp_get_training_summary

ROW = {
    "startWorkoutDate": "2026-10-05T00:00:00",
    "endWorkoutDate": "2026-10-11T00:00:00",
    "distancePlanned": 0.0,
    "distanceActual": 68674.7,
    "totalTimePlanned": 0.8083,
    "totalTimeActual": 2.9193,
    "totalTSSPlanned": 49.18,
    "totalTrainingStressScoreActual": 161.07,
    "averageIntensityFactorActual": 0.7449,
    "totalCaloriesBurned": 1978.0,
    "totalCaloriesPlanned": 0.0,
    "totalKilojoulesBurned": 1699.24,
    "totalElevationGainActual": 435.0,
    "totalElevationLossActual": 436.0,
    "workoutTypeId": 2,
}


async def _call(response, **kwargs):
    client = AsyncMock()
    client.ensure_athlete_id = AsyncMock(return_value=123)
    client.post = AsyncMock(return_value=response)
    client.get = AsyncMock(return_value=response)
    with patch("tp_mcp.tools.training_summary.TPClient") as mtp:
        mtp.return_value.__aenter__.return_value = client
        result = await tp_get_training_summary("2026-10-01", "2026-10-28", **kwargs)
    return result, client


class TestTrainingSummary:
    @pytest.mark.asyncio
    async def test_weekly_rows_are_converted(self):
        result, client = await _call(APIResponse(success=True, data={"dateIndices": [], "data": [ROW]}))
        endpoint = "/fitness/v1/athletes/123/reporting/workoutsummary/2026-10-01/2026-10-28"
        assert client.post.await_args.args[0] == endpoint
        assert client.post.await_args.kwargs["json"] == {"dateGrouping": 2, "groupByWorkoutType": False}
        row = result["rows"][0]
        assert row["start_date"] == "2026-10-05" and "sport" not in row
        assert row["distance_actual_km"] == 68.7
        assert row["duration_actual_hours"] == 2.92
        assert row["tss_planned"] == 49.2 and row["tss_actual"] == 161.1
        assert row["elevation_gain_m"] == 435

    @pytest.mark.asyncio
    async def test_by_sport_day(self):
        result, client = await _call(
            APIResponse(success=True, data={"data": [ROW]}), group_by="day", by_sport=True
        )
        assert client.post.await_args.kwargs["json"] == {"dateGrouping": 1, "groupByWorkoutType": True}
        assert result["rows"][0]["sport"] == "Bike"

    @pytest.mark.asyncio
    async def test_day_rows_use_workout_day(self):
        result, _ = await _call(
            APIResponse(success=True, data={"data": [dict(ROW, workoutDay="2026-10-06T00:00:00")]}), group_by="day"
        )
        assert result["rows"][0]["date"] == "2026-10-06"

    @pytest.mark.asyncio
    async def test_total_uses_fitnesssummary(self):
        unset = "0001-01-01T00:00:00"
        total = dict(ROW, startWorkoutDate=unset, endWorkoutDate=unset, workoutTypeId=999, totalTSSActual=161.07)
        del total["totalTrainingStressScoreActual"]
        result, client = await _call(APIResponse(success=True, data=[total]), group_by="total")
        assert client.get.await_args.args[0].endswith("/reporting/fitnesssummary/2026-10-01/2026-10-28")
        client.post.assert_not_awaited()
        row = result["rows"][0]
        assert "start_date" not in row and row["sport"] == "type_999" and row["tss_actual"] == 161.1

    @pytest.mark.asyncio
    async def test_api_error_and_validation(self):
        bad = APIResponse(success=False, error_code=ErrorCode.API_ERROR, message="boom")
        assert (await _call(bad))[0]["error_code"] == "API_ERROR"
        assert (await _call(bad, group_by="month"))[0]["error_code"] == "VALIDATION_ERROR"
        with patch("tp_mcp.tools.training_summary.TPClient"):
            r = await tp_get_training_summary("2024-01-01", "2026-10-01")
        assert r["error_code"] == "VALIDATION_ERROR"
