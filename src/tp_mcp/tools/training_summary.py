"""tp_get_training_summary - planned vs actual training totals by day, week or sport (dashboard report)."""

import logging
from datetime import date as date_type
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator, model_validator

from tp_mcp.client import TPClient
from tp_mcp.client.models import _sport_from_type_value
from tp_mcp.tools._validation import format_validation_error

logger = logging.getLogger("tp-mcp")

# dateGrouping values used by the web dashboard's workout summary (month is rejected by the API).
GROUPINGS: dict[str, int] = {"day": 1, "week": 2}
MAX_RANGE_DAYS = 366


class TrainingSummaryInput(BaseModel):
    """Validates input for the training summary report."""

    start_date: date_type
    end_date: date_type
    group_by: str = "week"
    by_sport: bool = False

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def coerce_date(cls, v: object) -> object:
        if isinstance(v, str):
            return date_type.fromisoformat(v)
        return v

    @field_validator("group_by")
    @classmethod
    def check_group_by(cls, v: str) -> str:
        if v not in (*GROUPINGS, "total"):
            raise ValueError(f"group_by must be one of: {', '.join((*GROUPINGS, 'total'))}")
        return v

    @model_validator(mode="after")
    def check_range(self) -> "TrainingSummaryInput":
        if self.start_date > self.end_date:
            raise ValueError("start_date must be before or equal to end_date")
        if (self.end_date - self.start_date).days > MAX_RANGE_DAYS:
            raise ValueError(f"Date range too large. Maximum {MAX_RANGE_DAYS} days.")
        return self


def _r(value: Any, digits: int = 1) -> Any:
    return round(value, digits) if isinstance(value, (int, float)) else value


def _format_row(entry: dict[str, Any], with_sport: bool) -> dict[str, Any]:
    row: dict[str, Any] = {}
    start = (entry.get("startWorkoutDate") or "").split("T")[0]
    day = (entry.get("workoutDay") or "").split("T")[0]
    if day:
        row["date"] = day
    elif start and not start.startswith("0001"):
        row["start_date"] = start
        row["end_date"] = (entry.get("endWorkoutDate") or "").split("T")[0]
    if with_sport:
        type_id = entry.get("workoutTypeId")
        row["sport"] = _sport_from_type_value(type_id) or f"type_{type_id}"
        row["workout_type_id"] = type_id
    distance_planned = entry.get("distancePlanned") or 0
    distance_actual = entry.get("distanceActual") or 0
    row.update(
        {
            "duration_planned_hours": _r(entry.get("totalTimePlanned"), 2),
            "duration_actual_hours": _r(entry.get("totalTimeActual"), 2),
            "distance_planned_km": _r(distance_planned / 1000),
            "distance_actual_km": _r(distance_actual / 1000),
            "tss_planned": _r(entry.get("totalTSSPlanned")),
            "tss_actual": _r(entry.get("totalTrainingStressScoreActual", entry.get("totalTSSActual"))),
            "intensity_factor_avg": _r(entry.get("averageIntensityFactorActual"), 2),
            "calories_burned": _r(entry.get("totalCaloriesBurned"), 0),
            "calories_planned": _r(entry.get("totalCaloriesPlanned"), 0),
            "energy_kj": _r(entry.get("totalKilojoulesBurned"), 0),
            "elevation_gain_m": _r(entry.get("totalElevationGainActual"), 0),
            "elevation_loss_m": _r(entry.get("totalElevationLossActual"), 0),
        }
    )
    return row


async def tp_get_training_summary(
    start_date: str,
    end_date: str,
    group_by: str = "week",
    by_sport: bool = False,
) -> dict[str, Any]:
    """Get planned vs actual training totals over a date range.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD). Maximum range 366 days.
        group_by: "day" or "week" for one row per day/week (weeks run Monday-Sunday and may
            extend past the requested range), or "total" for one row per sport over the range.
        by_sport: With "day"/"week", split each row by sport.

    Returns:
        Dict with rows of planned and actual duration (hours), distance (km), TSS, average
        intensity factor, calories, energy (kJ) and elevation (m), or an error envelope.
    """
    try:
        params = TrainingSummaryInput(start_date=start_date, end_date=end_date, group_by=group_by, by_sport=by_sport)
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

        base = f"/fitness/v1/athletes/{athlete_id}/reporting"
        span = f"{params.start_date.isoformat()}/{params.end_date.isoformat()}"
        if params.group_by == "total":
            response = await client.get(f"{base}/fitnesssummary/{span}")
        else:
            body = {"dateGrouping": GROUPINGS[params.group_by], "groupByWorkoutType": params.by_sport}
            response = await client.post(f"{base}/workoutsummary/{span}", json=body)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        data = response.data
        entries = data.get("data") if isinstance(data, dict) else data
        with_sport = params.group_by == "total" or params.by_sport
        rows = [_format_row(e, with_sport) for e in (entries or []) if isinstance(e, dict)]
        return {
            "group_by": params.group_by,
            "date_range": {"start": params.start_date.isoformat(), "end": params.end_date.isoformat()},
            "count": len(rows),
            "rows": rows,
        }
