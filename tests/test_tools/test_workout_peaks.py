"""Tests for tp_get_workout_peaks (detaildata)."""

from unittest.mock import AsyncMock, patch

import pytest

from tp_mcp.client.http import APIResponse, ErrorCode
from tp_mcp.tools.workout_peaks import tp_get_workout_peaks

DETAIL = {
    "workoutId": 55,
    "totalStats": {
        "powerBalanceLeft": 0.5028,
        "powerBalanceRight": 0.4972,
        "minMaxAvgs": {
            "power": {"min": 0.0, "max": 828.0, "avg": 160.0},
            "heartRate": {"min": 73.0, "max": 173.0, "avg": 132.0},
            "positionLat": {"min": 1.0, "max": 2.0, "avg": 1.5},
        },
    },
    "peakPowers": [
        {"begin": 8609000, "end": 8614000, "interval": 5, "value": 750},
        {"begin": 8576000, "end": 8578000, "interval": 2, "value": 768},
        {"begin": 8576000, "end": 8578000, "interval": 2, "value": 768},
    ],
    "peakHeartRates": [{"begin": 1000, "end": 3000, "interval": 2, "value": 173}],
    "peakSpeeds": [],
    "peakCadences": [],
    "peakSpeedsByDistance": [{"begin": 488000, "end": 645000, "interval": 400, "value": 2.5575161101711785}],
}


async def _call(response, workout_id="55"):
    client = AsyncMock()
    client.ensure_athlete_id = AsyncMock(return_value=123)
    client.get = AsyncMock(return_value=response)
    with patch("tp_mcp.tools.workout_peaks.TPClient") as mtp:
        mtp.return_value.__aenter__.return_value = client
        result = await tp_get_workout_peaks(workout_id)
    return result, client


class TestWorkoutPeaks:
    @pytest.mark.asyncio
    async def test_formats_peaks_stats_and_balance(self):
        result, client = await _call(APIResponse(success=True, data=DETAIL))
        assert client.get.await_args.args[0] == "/fitness/v6/athletes/123/workouts/55/detaildata"
        assert result["peaks"]["power"] == [
            {"duration_s": 2, "value": 768, "start_s": 8576},
            {"duration_s": 5, "value": 750, "start_s": 8609},
        ]
        assert result["peaks"]["heart_rate"][0]["value"] == 173
        assert "speed" not in result["peaks"]
        assert result["peak_speed_by_distance"] == [{"distance_m": 400, "speed_ms": 2.558, "start_s": 488}]
        assert result["channel_stats"]["power"] == {"min": 0.0, "max": 828.0, "avg": 160.0}
        assert "positionLat" not in result["channel_stats"]
        assert result["power_balance"] == {"left_percent": 50.3, "right_percent": 49.7}

    @pytest.mark.asyncio
    async def test_no_balance_when_not_recorded(self):
        result, _ = await _call(APIResponse(success=True, data={"workoutId": 1, "totalStats": {}}))
        assert "power_balance" not in result and result["peaks"] == {}

    @pytest.mark.asyncio
    async def test_errors(self):
        bad = APIResponse(success=False, error_code=ErrorCode.NOT_FOUND, message="gone")
        assert (await _call(bad))[0]["isError"] is True
        assert (await _call(bad, workout_id="abc"))[0]["error_code"] == "VALIDATION_ERROR"
