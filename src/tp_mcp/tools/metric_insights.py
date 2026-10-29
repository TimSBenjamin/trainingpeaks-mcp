"""tp_get_metric_insights - trend summaries for health metrics (RHR, sleep, HRV, weight...).

Reads the Peaksware health-metrics service (`api.peakswaresb.com`, same Bearer
token as the other tools). Endpoint and response keys were recorded from the web
app: GET /health-metrics/v1/metrics/insights?athleteId=&startDate=&endDate=
"""

import logging
from typing import Any

import httpx

from tp_mcp.client import TPClient
from tp_mcp.tools._validation import DateRangeInput, format_validation_error
from tp_mcp.tools.metrics import METRIC_TYPES

logger = logging.getLogger("tp-mcp")

HEALTH_API_BASE = "https://api.peakswaresb.com"
INSIGHTS_PATH = "/health-metrics/v1/metrics/insights"
TIMEOUT = 30.0

_TYPE_NAMES = {v["type"]: k for k, v in METRIC_TYPES.items()}


def _err(code: str, message: str) -> dict[str, Any]:
    return {"isError": True, "error_code": code, "message": message}


def _round(value: Any) -> Any:
    return round(value, 2) if isinstance(value, float) else value


def _format_insight(raw: dict[str, Any]) -> dict[str, Any]:
    type_id = raw.get("typeId")
    status = raw.get("periodEndStatus") or {}
    return {
        "metric": _TYPE_NAMES.get(type_id, f"type_{type_id}"),
        "type_id": type_id,
        "source": raw.get("source"),
        "period_start": (raw.get("periodStartDate") or "").split("T")[0],
        "period_start_value": raw.get("periodStartValue"),
        "period_end": (raw.get("periodEndDate") or "").split("T")[0],
        "period_end_value": raw.get("periodEndValue"),
        "period_days": raw.get("periodDurationDays"),
        "change_percent": raw.get("periodDeltaPercentage"),
        "previous_date": (raw.get("previousDate") or "").split("T")[0],
        "previous_value": raw.get("previousDateValue"),
        "previous_delta": _round(raw.get("previousDateDelta")),
        "status": status.get("name"),
        "status_description": status.get("description"),
    }


async def tp_get_metric_insights(start_date: str, end_date: str) -> dict[str, Any]:
    """Get health-metric trend insights (period start/end values, change, status).

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD). Maximum range 90 days.

    Returns:
        Dict with one insight per metric type, or an error envelope. `status` is the
        API's status name for the latest reading (seen: WithinRange, Above,
        NotSyncedRecently) and `status_description` its text, e.g. the normal range
        ("48 - 51"), "Above Normal Range" or "3 days since last sync".
    """
    try:
        params = DateRangeInput(start_date=start_date, end_date=end_date)
    except ValueError as e:
        return _err("VALIDATION_ERROR", format_validation_error(e) if hasattr(e, "errors") else str(e))

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return _err("AUTH_INVALID", "Could not get athlete ID. Re-authenticate.")
        token = await client._ensure_access_token()
        access = client._token_cache.access_token
        if not token.success or not access:
            return _err("AUTH_INVALID", token.message or "Failed to obtain access token.")

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as h:
                r = await h.get(
                    f"{HEALTH_API_BASE}{INSIGHTS_PATH}",
                    params={
                        "athleteId": athlete_id,
                        "startDate": params.start_date.isoformat(),
                        "endDate": params.end_date.isoformat(),
                    },
                    headers={
                        "Authorization": f"Bearer {access}",
                        "Accept": "application/json",
                        "Origin": "https://app.trainingpeaks.com",
                        "Referer": "https://app.trainingpeaks.com/",
                    },
                )
        except httpx.TimeoutException:
            return _err("NETWORK_ERROR", "Metric insights request timed out.")
        except httpx.RequestError:
            logger.exception("Network error fetching metric insights")
            return _err("NETWORK_ERROR", "A network error occurred.")

        if r.status_code == 401:
            return _err("AUTH_EXPIRED", "Session expired. Run 'tp-mcp auth' to re-authenticate.")
        if r.status_code == 403:
            return _err("AUTH_INVALID", "Access denied. Check permissions or re-authenticate.")
        if r.status_code != 200:
            return _err("API_ERROR", f"Metric insights API error: {r.status_code}")

        try:
            body = r.json()
        except ValueError:
            return _err("API_ERROR", "Failed to parse metric insights response.")

        items = body.get("metricInsights") if isinstance(body, dict) else None
        insights = [_format_insight(i) for i in items or [] if isinstance(i, dict)]
        return {
            "insights": insights,
            "count": len(insights),
            "date_range": {"start": params.start_date.isoformat(), "end": params.end_date.isoformat()},
        }
