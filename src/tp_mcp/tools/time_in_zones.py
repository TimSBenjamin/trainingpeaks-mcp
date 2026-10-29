"""tp_get_time_in_zones - minutes spent in each training zone (dashboard report)."""

import logging
from datetime import date as date_type
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from tp_mcp.client import TPClient
from tp_mcp.tools._validation import format_validation_error

logger = logging.getLogger("tp-mcp")

# timeInZonesType values used by the web dashboard's time-in-zones charts.
ZONE_TYPES: dict[str, int] = {"heart_rate": 1, "power": 2, "speed": 3}
MAX_RANGE_DAYS = 366


class TimeInZonesInput(BaseModel):
    """Validates input for the time-in-zones report."""

    start_date: date_type
    end_date: date_type
    zone_type: str = "heart_rate"
    by_week: bool = False
    workout_type_ids: list[int] = Field(default_factory=list)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def coerce_date(cls, v: object) -> object:
        if isinstance(v, str):
            return date_type.fromisoformat(v)
        return v

    @field_validator("zone_type")
    @classmethod
    def check_zone_type(cls, v: str) -> str:
        if v not in ZONE_TYPES:
            raise ValueError(f"zone_type must be one of: {', '.join(ZONE_TYPES)}")
        return v

    @model_validator(mode="after")
    def check_range(self) -> "TimeInZonesInput":
        if self.start_date > self.end_date:
            raise ValueError("start_date must be before or equal to end_date")
        if (self.end_date - self.start_date).days > MAX_RANGE_DAYS:
            raise ValueError(f"Date range too large. Maximum {MAX_RANGE_DAYS} days.")
        return self


def _format_period(entry: dict[str, Any]) -> dict[str, Any]:
    """Collapse zone1Minutes..zone10Minutes into a list ending at the last used zone."""
    minutes = [entry.get(f"zone{i}Minutes") or 0 for i in range(1, 11)]
    last_used = max((i for i, m in enumerate(minutes, start=1) if m), default=0)
    zones = [{"zone": i, "minutes": round(minutes[i - 1], 1)} for i in range(1, last_used + 1)]
    return {
        "start_date": (entry.get("startDate") or "").split("T")[0],
        "end_date": (entry.get("endDate") or "").split("T")[0],
        "total_minutes": round(sum(minutes), 1),
        "zones": zones,
    }


async def tp_get_time_in_zones(
    start_date: str,
    end_date: str,
    zone_type: str = "heart_rate",
    by_week: bool = False,
    workout_type_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Get minutes spent in each training zone over a date range.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        zone_type: "heart_rate", "power" or "speed".
        by_week: Return one row per week instead of a single total.
        workout_type_ids: Restrict to these workout type ids (see tp_get_workout_types).

    Returns:
        Dict with per-zone minutes, or an error envelope.
    """
    try:
        params = TimeInZonesInput(
            start_date=start_date,
            end_date=end_date,
            zone_type=zone_type,
            by_week=by_week,
            workout_type_ids=workout_type_ids or [],
        )
    except (ValidationError, ValueError) as e:
        msg = format_validation_error(e) if isinstance(e, ValidationError) else str(e)
        return {"isError": True, "error_code": "VALIDATION_ERROR", "message": msg}

    async with TPClient() as client:
        athlete_id = await client.ensure_athlete_id()
        if not athlete_id:
            return {
                "isError": True,
                "error_code": "AUTH_INVALID",
                "message": "Could not get athlete ID. Re-authenticate.",
            }

        report = "timeinzonesbyweek" if params.by_week else "timeinzones"
        endpoint = (
            f"/fitness/v1/athletes/{athlete_id}/reporting/{report}/"
            f"{params.start_date.isoformat()}/{params.end_date.isoformat()}"
        )
        body = {
            "workoutTypeIds": [str(i) for i in params.workout_type_ids],
            "timeInZonesType": ZONE_TYPES[params.zone_type],
        }
        response = await client.post(endpoint, json=body)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        data = response.data
        entries = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        periods = [_format_period(e) for e in entries if isinstance(e, dict)]

        result: dict[str, Any] = {
            "zone_type": params.zone_type,
            "date_range": {"start": params.start_date.isoformat(), "end": params.end_date.isoformat()},
        }
        if params.by_week:
            result["weeks"] = periods
            result["count"] = len(periods)
        else:
            result.update(periods[0] if periods else {"total_minutes": 0, "zones": []})
        return result
