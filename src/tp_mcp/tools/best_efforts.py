"""tp_get_best_efforts - best sustained efforts (power/HR/speed/cadence curve) over a date range."""

import logging
import re
from datetime import date as date_type
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from tp_mcp.client import TPClient
from tp_mcp.tools._validation import format_validation_error

logger = logging.getLogger("tp-mcp")

# meanMaxBestsType values used by the web dashboard's mean-max charts.
METRICS: dict[str, int] = {"heart_rate": 1, "power": 2, "speed": 3, "pace_by_distance": 4, "cadence": 5}
MAX_RANGE_DAYS = 366

_UNIT_SECONDS = {"Second": 1, "Minute": 60, "Hour": 3600}
_UNIT_METRES = {"Kilometer": 1000, "Mile": 1609.344}
_LABEL = re.compile(r"^MM(\d+)([A-Za-z]+?)s?$")


class BestEffortsInput(BaseModel):
    """Validates input for the mean-max (best efforts) report."""

    start_date: date_type
    end_date: date_type
    metric: str = "power"
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

    @model_validator(mode="after")
    def check_range(self) -> "BestEffortsInput":
        if self.start_date > self.end_date:
            raise ValueError("start_date must be before or equal to end_date")
        if (self.end_date - self.start_date).days > MAX_RANGE_DAYS:
            raise ValueError(f"Date range too large. Maximum {MAX_RANGE_DAYS} days.")
        return self


def _parse_label(label: str) -> tuple[str, float] | None:
    """Turn e.g. 'MM20Minutes' into ('20 min', 1200) and 'MM10Kilometer' into ('10 km', 10000)."""
    m = _LABEL.match(label or "")
    if not m:
        return None
    amount, unit = int(m.group(1)), m.group(2)
    if unit in _UNIT_SECONDS:
        seconds = amount * _UNIT_SECONDS[unit]
        short = {"Second": "s", "Minute": "min", "Hour": "h"}[unit]
        return f"{amount} {short}", seconds
    if unit in _UNIT_METRES:
        short = {"Kilometer": "km", "Mile": "mi"}[unit]
        return f"{amount} {short}", amount * _UNIT_METRES[unit]
    return None


def _format_effort(entry: dict[str, Any]) -> dict[str, Any] | None:
    parsed = _parse_label(entry.get("label") or "")
    if parsed is None:
        return None
    duration, size = parsed
    if not entry.get("value"):
        return None  # distance/duration never reached in this window
    return {
        "duration": duration,
        "size": size,
        "value": entry.get("value"),
        "workout_id": str(entry.get("workoutId")),
        "date": (entry.get("date") or "").split("T")[0],
    }


async def tp_get_best_efforts(
    start_date: str,
    end_date: str,
    metric: str = "power",
    workout_type_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Get the best efforts (mean-max curve) over a date range.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        metric: "power", "heart_rate", "speed", "cadence" (best average over 5 s ... 3 h)
            or "pace_by_distance" (best speed over 1 km ... 100 mi). Speeds are in m/s;
            durations or distances never reached in the window are omitted.
        workout_type_ids: Restrict to these workout type ids (see tp_get_workout_types).

    Returns:
        Dict with `efforts` ordered shortest to longest; each has the best value, the
        workout that set it and its date. Or an error envelope.
    """
    try:
        params = BestEffortsInput(
            start_date=start_date,
            end_date=end_date,
            metric=metric,
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
            f"/fitness/v1/athletes/{athlete_id}/reporting/meanmaxbests/"
            f"{params.start_date.isoformat()}/{params.end_date.isoformat()}"
        )
        body = {
            "workoutTypeIds": [str(i) for i in params.workout_type_ids],
            "meanMaxBestsType": METRICS[params.metric],
            "tags": [],
        }
        response = await client.post(endpoint, json=body)

        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }

        data = response.data if isinstance(response.data, dict) else {}
        efforts = [x for x in (_format_effort(m) for m in data.get("meanMaxes") or []) if x]
        efforts.sort(key=lambda x: x["size"])
        for effort in efforts:
            del effort["size"]
        return {
            "metric": params.metric,
            "date_range": {"start": params.start_date.isoformat(), "end": params.end_date.isoformat()},
            "count": len(efforts),
            "efforts": efforts,
        }
