"""Tests for tp_get_best_efforts (mean-max report)."""

from unittest.mock import AsyncMock, patch

import pytest

from tp_mcp.client.http import APIResponse, ErrorCode
from tp_mcp.tools.best_efforts import tp_get_best_efforts

MEAN_MAXES = {
    "personId": 1,
    "startDate": "2026-10-01T00:00:00",
    "endDate": "2026-10-28T00:00:00",
    "meanMaxes": [
        {"workoutId": 7, "date": "2026-10-10T00:00:00", "label": "MM20Minutes", "value": 250},
        {"workoutId": 8, "date": "2026-10-12T00:00:00", "label": "MM5Seconds", "value": 900},
        {"workoutId": 8, "date": "2026-10-12T00:00:00", "label": "MM1Minute", "value": 400},
        {"workoutId": 9, "date": "2026-10-14T00:00:00", "label": "MM1Hour", "value": 210},
        {"workoutId": 9, "date": "2026-10-14T00:00:00", "label": "MMWhoKnows", "value": 1},
    ],
}


async def _call(response, **kwargs):
    client = AsyncMock()
    client.ensure_athlete_id = AsyncMock(return_value=123)
    client.post = AsyncMock(return_value=response)
    with patch("tp_mcp.tools.best_efforts.TPClient") as mtp:
        mtp.return_value.__aenter__.return_value = client
        result = await tp_get_best_efforts("2026-10-01", "2026-10-28", **kwargs)
    return result, client


class TestBestEfforts:
    @pytest.mark.asyncio
    async def test_orders_curve_and_skips_unknown_labels(self):
        result, client = await _call(APIResponse(success=True, data=MEAN_MAXES))
        assert [e["duration"] for e in result["efforts"]] == ["5 s", "1 min", "20 min", "1 h"]
        assert result["efforts"][0] == {"duration": "5 s", "value": 900, "workout_id": "8", "date": "2026-10-12"}
        assert result["count"] == 4 and result["metric"] == "power"
        endpoint = client.post.await_args.args[0]
        assert endpoint == "/fitness/v1/athletes/123/reporting/meanmaxbests/2026-10-01/2026-10-28"
        assert client.post.await_args.kwargs["json"] == {"workoutTypeIds": [], "meanMaxBestsType": 2, "tags": []}

    @pytest.mark.asyncio
    async def test_metric_and_sport_filter(self):
        _, client = await _call(
            APIResponse(success=True, data={"meanMaxes": []}), metric="heart_rate", workout_type_ids=[2]
        )
        assert client.post.await_args.kwargs["json"] == {"workoutTypeIds": ["2"], "meanMaxBestsType": 1, "tags": []}

    @pytest.mark.asyncio
    async def test_distance_labels(self):
        data = {"meanMaxes": [
            {"workoutId": 1, "date": "2026-10-01T00:00:00", "label": "MM10Kilometer", "value": 4.1},
            {"workoutId": 1, "date": "2026-10-01T00:00:00", "label": "MM1Mile", "value": 4.5},
            {"workoutId": 1, "date": "2026-10-01T00:00:00", "label": "MM100Mile", "value": 0.0},
        ]}
        result, _ = await _call(APIResponse(success=True, data=data), metric="pace_by_distance")
        assert [e["duration"] for e in result["efforts"]] == ["1 mi", "10 km"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        bad = APIResponse(success=False, error_code=ErrorCode.API_ERROR, message="boom")
        result, _ = await _call(bad)
        assert result["isError"] and result["error_code"] == "API_ERROR"

    @pytest.mark.asyncio
    async def test_validation(self):
        r, _ = await _call(APIResponse(success=True, data={}), metric="vo2")
        assert r["error_code"] == "VALIDATION_ERROR"
        with patch("tp_mcp.tools.best_efforts.TPClient"):
            r = await tp_get_best_efforts("2026-10-28", "2026-10-01")
        assert r["error_code"] == "VALIDATION_ERROR"
        with patch("tp_mcp.tools.best_efforts.TPClient"):
            r = await tp_get_best_efforts("2024-01-01", "2026-10-01")
        assert r["error_code"] == "VALIDATION_ERROR"
