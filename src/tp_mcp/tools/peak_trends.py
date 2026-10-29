"""tp_get_peak_trends - best 5s/1m/5m/20m/60m efforts per week or month (dashboard fitness history)."""

import logging
from datetime import date as date_type
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from tp_mcp.client import TPClient
from tp_mcp.tools._validation import format_validation_error

logger = logging.getLogger("tp-mcp")

# peakType values used by the web dashboard's fitness history report.
METRICS: dict[str, int] = {"heart_rate": 1, "speed": 2, "power": 4, "cadence": 5, "pace_by_distance": 6}
GROUPS: dict[str, int] = {"week": 0, "month": 1}
# peak01..peak05 line up with these durations (checked against the mean-max report).
PEAK_DURATIONS = ("5 s", "1 min", "5 min", "20 min", "60 min")
# pace_by_distance (peakType 6) reports run speed over these distances instead.
PEAK_DISTANCES = ("400 m", "1 km", "1 mile", "5 km", "10 km")
MAX_RANGE_DAYS = 731


class PeakTrendsInput(BaseModel):
    """Validates input for the peak trends report."""

    start_date: date_type
    end_date: date_type
    metric: str = "power"
    group_by: str = "week"
    workout_type_ids: list[int] = Field(default_factory=list)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def coerce_date(cls, v: object) -> object:
        if isinstance(v, str):
            return date_type.fromisoformat(v)
        return v

    @field_validator("metric")
    @classmethod
    def check_metric(cls, v: str) -> str:
        if v not in METRICS:
            raise ValueError(f"metric must be one of: {', '.join(METRICS)}")
        return v

    @field_validator("group_by")
    @classmethod
    def check_group_by(cls, v: str) -> str:
        if v not in GROUPS:
            raise ValueError(f"group_by must be one of: {', '.join(GROUPS)}")
        return v

    @model_validator(mode="after")
    def check_range(self) -> "PeakTrendsInput":
        if self.start_date > self.end_date:
            raise ValueError("start_date must be before or equal to end_date")
        if (self.end_date - self.start_date).days > MAX_RANGE_DAYS:
            raise ValueError(f"Date range too large. Maximum {MAX_RANGE_DAYS} days.")
        return self


def _round(value: Any, digits: int) -> Any:
    return round(value, digits) if isinstance(value, (int, float)) else value


def _format_period(entry: dict[str, Any], labels: tuple[str, ...] = PEAK_DURATIONS) -> dict[str, Any]:
    peaks = []
    for i, duration in enumerate(labels, start=1):
        value = entry.get(f"peak0{i}")
        if value is None:
            continue
        workout_ids = entry.get(f"peak0{i}WorkoutIds") or []
        peaks.append(
            {
                "duration": duration,
                "value": round(value, 2),
                "workout_id": str(workout_ids[0]) if workout_ids else None,
            }
        )
    distance = entry.get("totalDistance") or 0
    duration_hours = entry.get("totalDuration")
    return {
        "start_date": (entry.get("startDate") or "").split("T")[0],
        "end_date": (entry.get("endDate") or "").split("T")[0],
        "distance_km": round(distance / 1000, 1),
        "duration_hours": round(duration_hours, 2) if isinstance(duration_hours, (int, float)) else None,
        "tss": _round(entry.get("totalTSS"), 1),
        "energy_kj": _round(entry.get("totalKJ"), 0),
        "peaks": peaks,
    }


async def tp_get_peak_trends(
    start_date: str,
    end_date: str,
    metric: str = "power",
    group_by: str = "week",
    workout_type_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Get the best 5 s / 1 min / 5 min / 20 min / 60 min efforts for each week or month.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD). Maximum range 731 days.
        metric: "power", "heart_rate", "speed" (m/s) or "cadence"; "pace_by_distance" gives
            run speed (m/s) over 400 m, 1 km, 1 mile, 5 km and 10 km instead of the time windows.
        group_by: "week" (Monday-Sunday, may extend past the range) or "month".
        workout_type_ids: Restrict to these workout type ids (see tp_get_workout_types).

    Returns:
        Dict with one row per period: training totals and the five peaks with the
        workout that set each, or an error envelope.
    """
    try:
        params = PeakTrendsInput(
            start_date=start_date,
            end_date=end_date,
            metric=metric,
            group_by=group_by,
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

        endpoint = (
            f"/fitness/v1/athletes/{athlete_id}/reporting/fitnesshistory/"
            f"{params.start_date.isoformat()}/{params.end_date.isoformat()}"
        )
        body = {
            "group": GROUPS[params.group_by],
            "peakType": METRICS[params.metric],
            "workoutTypeIds": [str(i) for i in params.workout_type_ids],
        }
        response = await client.post(endpoint, json=body)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        entries = response.data if isinstance(response.data, list) else []
        labels = PEAK_DISTANCES if params.metric == "pace_by_distance" else PEAK_DURATIONS
        periods = [_format_period(e, labels) for e in entries if isinstance(e, dict)]
        return {
            "metric": params.metric,
            "group_by": params.group_by,
            "date_range": {"start": params.start_date.isoformat(), "end": params.end_date.isoformat()},
            "count": len(periods),
            "periods": periods,
        }
