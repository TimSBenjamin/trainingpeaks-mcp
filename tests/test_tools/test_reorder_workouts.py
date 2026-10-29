"""Tests for tp_reorder_workouts (calendar order endpoint, endurance + strength)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tp_mcp.client.http import APIResponse, ErrorCode
from tp_mcp.client.models import WorkoutSummary
from tp_mcp.tools.workouts import tp_reorder_workouts


def _tp_client(post=None):
    client = AsyncMock()
    client.ensure_athlete_id = AsyncMock(return_value=123)
    client._ensure_access_token = AsyncMock(return_value=MagicMock(success=True))
    client._token_cache = MagicMock(access_token="tok")
    client.post = AsyncMock(return_value=post or APIResponse(success=True, data={}))
    return client


def _strength_http(status=200, body=None):
    resp = MagicMock(status_code=status)
    resp.json.return_value = body if body is not None else []
    h = AsyncMock()
    h.get = AsyncMock(return_value=resp)
    return h


async def _call(tp, h=None, **kwargs):
    with patch("tp_mcp.tools.workouts.TPClient") as mtp, patch("httpx.AsyncClient") as mh:
        mtp.return_value.__aenter__.return_value = tp
        mh.return_value.__aenter__.return_value = h or _strength_http()
        return await tp_reorder_workouts(**kwargs)


class TestReorderWorkouts:
    @pytest.mark.asyncio
    async def test_endurance_only_posts_calendar_order(self):
        tp = _tp_client()
        result = await _call(tp, workout_ids=[11, 22])
        assert result["success"] is True
        tp.post.assert_awaited_once_with(
            "/fitness/v6/athletes/123/calendar/order",
            json={"workouts": [{"id": 11, "order": 0}, {"id": 22, "order": 1}]},
        )

    @pytest.mark.asyncio
    async def test_strength_ids_are_split_out_for_the_day(self):
        tp = _tp_client()
        h = _strength_http(body=[{"id": "900", "title": "Upper"}])
        result = await _call(tp, h, workout_ids=[11, "900", 22], date="2026-10-29")
        assert result["success"] is True
        assert "/rx/activity/v1/workouts/calendar/123/2026-10-29/2026-10-29" in h.get.await_args.args[0]
        assert tp.post.await_args.kwargs["json"] == {
            "workouts": [{"id": 11, "order": 0}, {"id": 22, "order": 2}],
            "strengthWorkouts": [{"id": "900", "order": 1}],
        }

    @pytest.mark.asyncio
    async def test_strength_list_failure_does_not_write(self):
        tp = _tp_client()
        result = await _call(tp, _strength_http(status=500), workout_ids=[1], date="2026-10-29")
        assert result["error_code"] == "API_ERROR"
        tp.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_post_error_is_reported(self):
        bad = APIResponse(success=False, error_code=ErrorCode.API_ERROR, message="nope")
        result = await _call(_tp_client(post=bad), workout_ids=[1])
        assert result["error_code"] == "API_ERROR" and "nope" in result["message"]

    @pytest.mark.asyncio
    async def test_validation(self):
        assert (await _call(_tp_client(), workout_ids=[]))["error_code"] == "VALIDATION_ERROR"
        assert (await _call(_tp_client(), workout_ids=[1], date="29/10"))["error_code"] == "VALIDATION_ERROR"


def test_workout_summary_exposes_order_on_day():
    w = WorkoutSummary.model_validate({"workoutId": 1, "workoutDay": "2026-10-29", "orderOnDay": 2})
    assert w.order_on_day == 2
