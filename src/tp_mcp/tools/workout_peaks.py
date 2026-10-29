"""tp_get_workout_peaks - best efforts inside one workout, power balance and per-channel stats (detaildata)."""

import logging
from typing import Any

from pydantic import ValidationError

from tp_mcp.client import TPClient
from tp_mcp.tools._validation import WorkoutIdInput, format_validation_error

logger = logging.getLogger("tp-mcp")

_PEAK_CHANNELS = {
    "peakPowers": "power",
    "peakHeartRates": "heart_rate",
    "peakSpeeds": "speed",
    "peakCadences": "cadence",
}
_STAT_CHANNELS = {
    "power": "power",
    "heartRate": "heart_rate",
    "cadence": "cadence",
    "speed": "speed",
    "elevation": "elevation",
    "temperature": "temperature",
    "powerLeft": "power_left",
    "powerRight": "power_right",
}


def _format_peaks(entries: Any) -> list[dict[str, Any]]:
    """Dedupe by window length (the API repeats them) and order shortest to longest."""
    best: dict[int, dict[str, Any]] = {}
    for p in entries or []:
        interval = p.get("interval")
        if interval is None or interval in best:
            continue
        best[interval] = {
            "duration_s": interval,
            "value": round(p["value"], 2) if isinstance(p.get("value"), float) else p.get("value"),
            "start_s": round((p.get("begin") or 0) / 1000),
        }
    return [best[k] for k in sorted(best)]


def _format_by_distance(entries: Any) -> list[dict[str, Any]]:
    best: dict[int, dict[str, Any]] = {}
    for p in entries or []:
        interval = p.get("interval")
        if interval is None or interval in best:
            continue
        best[interval] = {
            "distance_m": interval,
            "speed_ms": round(p["value"], 3) if isinstance(p.get("value"), float) else p.get("value"),
            "start_s": round((p.get("begin") or 0) / 1000),
        }
    return [best[k] for k in sorted(best)]


def _format_detail(data: dict[str, Any]) -> dict[str, Any]:
    stats = data.get("totalStats") or {}
    mma = stats.get("minMaxAvgs") or {}
    channel_stats = {
        name: {k: mma[key].get(k) for k in ("min", "max", "avg")}
        for key, name in _STAT_CHANNELS.items()
        if isinstance(mma.get(key), dict)
    }
    peaks = {name: _format_peaks(data.get(key)) for key, name in _PEAK_CHANNELS.items()}
    result: dict[str, Any] = {
        "workout_id": str(data.get("workoutId")),
        "peaks": {name: p for name, p in peaks.items() if p},
        "channel_stats": channel_stats,
    }
    by_distance = _format_by_distance(data.get("peakSpeedsByDistance"))
    if by_distance:
        result["peak_speed_by_distance"] = by_distance
    left, right = stats.get("powerBalanceLeft"), stats.get("powerBalanceRight")
    if left is not None and right is not None:
        result["power_balance"] = {"left_percent": round(left * 100, 1), "right_percent": round(right * 100, 1)}
    return result


async def tp_get_workout_peaks(workout_id: str) -> dict[str, Any]:
    """Get the best efforts within one completed workout, plus power balance and channel stats.

    For each channel (power, heart rate, speed, cadence) returns the best average over
    windows from 1-2 s up to 3 h and when in the workout it started; runs also get the
    fastest speed over set distances. Also left/right power balance and min/max/avg per
    channel. Laps, series and derived metrics are in tp_analyze_workout.

    Args:
        workout_id: The workout ID (from tp_get_workouts).

    Returns:
        Dict with peaks, channel_stats and power_balance (when recorded), or an error envelope.
    """
    try:
        validated = WorkoutIdInput(workout_id=workout_id)  # type: ignore[arg-type]
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

        response = await client.get(f"/fitness/v6/athletes/{athlete_id}/workouts/{validated.workout_id}/detaildata")
        if response.is_error:
            return {
                "isError": True,
                "error_code": response.error_code.value if response.error_code else "API_ERROR",
                "message": response.message,
            }
        if not isinstance(response.data, dict):
            return {"isError": True, "error_code": "API_ERROR", "message": "Unexpected detaildata response."}
        return _format_detail(response.data)
