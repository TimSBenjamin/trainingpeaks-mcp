"""Tests for tp_get_metric_insights.

The response fixture is built from the key names recorded from the web app;
the values are synthetic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tp_mcp.client.http import APIResponse
from tp_mcp.tools.metric_insights import tp_get_metric_insights

BODY = {
    "metricInsightsPreferences": {"insightsSourcePreferences": []},
    "metricInsights": [
        {
            "typeId": 5,
            "source": "Garmin",
            "periodStartDate": "2026-01-01T00:00:00",
            "periodStartValue": 52.0,
            "periodEndDate": "2026-01-31T00:00:00",
            "periodEndValue": 49.0,
            "periodEndStatus": {"id": 1, "name": "WithinRange", "description": "48 - 51"},
            "previousDate": "2025-12-31T00:00:00",
            "previousDateValue": 50.0,
            "previousDateDelta": 0.08000000000000007,
            "periodDurationDays": 30,
            "periodDeltaPercentage": -5.8,
            "details": [],
        },
        {"typeId": 999, "source": "Manual", "periodEndStatus": None},
    ],
}


def _tp_client():
    client = AsyncMock()
    client.ensure_athlete_id = AsyncMock(return_value=123)
    client._ensure_access_token = AsyncMock(return_value=APIResponse(success=True))
    client._token_cache = MagicMock(access_token="tok")
    return client


def _http(status=200, body=None, exc=None):
    resp = MagicMock(status_code=status)
    resp.json = MagicMock(return_value=body)
    h = AsyncMock()
    if exc:
        h.get = AsyncMock(side_effect=exc)
    else:
        h.get = AsyncMock(return_value=resp)
    return h


async def _call(h, tp=None):
    with patch("tp_mcp.tools.metric_insights.TPClient") as mtp, patch(
        "tp_mcp.tools.metric_insights.httpx.AsyncClient"
    ) as mh:
        mtp.return_value.__aenter__.return_value = tp or _tp_client()
        mh.return_value.__aenter__.return_value = h
        return await tp_get_metric_insights("2026-01-01", "2026-01-31")


class TestMetricInsights:
    @pytest.mark.asyncio
    async def test_maps_insights(self):
        h = _http(body=BODY)
        result = await _call(h)
        assert result["count"] == 2
        first = result["insights"][0]
        assert first["metric"] == "pulse"
        assert first["period_start"] == "2026-01-01"
        assert first["change_percent"] == -5.8
        assert first["status"] == "WithinRange"
        assert first["previous_delta"] == 0.08
        assert result["insights"][1]["metric"] == "type_999"
        kwargs = h.get.await_args.kwargs
        assert kwargs["params"] == {"athleteId": 123, "startDate": "2026-01-01", "endDate": "2026-01-31"}
        assert kwargs["headers"]["Authorization"] == "Bearer tok"

    @pytest.mark.asyncio
    async def test_empty(self):
        result = await _call(_http(body={"metricInsights": []}))
        assert result["count"] == 0 and result["insights"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,code", [(401, "AUTH_EXPIRED"), (403, "AUTH_INVALID"), (500, "API_ERROR")])
    async def test_http_errors(self, status, code):
        result = await _call(_http(status=status))
        assert result["isError"] and result["error_code"] == code

    @pytest.mark.asyncio
    async def test_timeout_and_network(self):
        assert (await _call(_http(exc=httpx.ReadTimeout("t"))))["error_code"] == "NETWORK_ERROR"
        assert (await _call(_http(exc=httpx.ConnectError("c"))))["error_code"] == "NETWORK_ERROR"

    @pytest.mark.asyncio
    async def test_validation(self):
        result = await tp_get_metric_insights("2026-02-01", "2026-01-01")
        assert result["error_code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_no_athlete(self):
        tp = _tp_client()
        tp.ensure_athlete_id = AsyncMock(return_value=None)
        result = await _call(_http(body=BODY), tp)
        assert result["error_code"] == "AUTH_INVALID"
