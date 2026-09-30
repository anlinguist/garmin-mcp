"""Response shaping: drop bulky arrays unless full=true, then cap total size."""

from __future__ import annotations

import json
from typing import Any

# Keys that are almost always large time series / geometry.
BULKY_KEYS = {
    "geoPolylineDTO", "polyline", "activityDetailMetrics", "metricDescriptors",
    "heartRateValues", "heartRateValueDescriptors", "stressValuesArray",
    "stressValueDescriptorsDTOList", "bodyBatteryValuesArray",
    "bodyBatteryValueDescriptorDTOList", "bodyBatteryValueDescriptorsDTOList",
    "sleepMovement", "sleepLevels", "sleepHeartRate", "sleepStress",
    "sleepBodyBattery", "hrvReadings", "sleepRestlessMoments", "wellnessEpochRespirationDataDTOList",
    "respirationValuesArray", "respirationAveragesValuesArray",
    "respirationValueDescriptorsDTOList", "spO2HourlyAverages", "spO2SingleValues",
    "continuousReadingDTOList", "wellnessSpO2SleepSummaryDTO", "hrTimeInZones",
    "chartData", "lactateThresholdHeartRateSamples",
}
# Any list longer than this made only of numbers / small numeric lists is a series.
SERIES_MIN_LEN = 60
DEFAULT_LIST_KEEP = 25


def _is_numeric_series(value: list[Any]) -> bool:
    sample = value[:20]
    return all(
        isinstance(x, int | float | type(None))
        or (isinstance(x, list) and all(isinstance(y, int | float | type(None)) for y in x))
        for x in sample
    )


def _omitted(value: Any) -> dict[str, Any]:
    n = len(value) if hasattr(value, "__len__") else None
    return {"_omitted": f"{type(value).__name__} with {n} items; pass full=true to include"}


def drop_bulky(value: Any, notes: set[str]) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in BULKY_KEYS and v not in (None, [], {}):
                out[k] = _omitted(v)
                notes.add(k)
            else:
                out[k] = drop_bulky(v, notes)
        return out
    if isinstance(value, list):
        if len(value) >= SERIES_MIN_LEN and _is_numeric_series(value):
            notes.add("numeric_series")
            return _omitted(value)
        return [drop_bulky(v, notes) for v in value]
    return value


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _trim_lists(value: Any, keep: int) -> tuple[Any, bool]:
    trimmed = False
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k], t = _trim_lists(v, keep)
            trimmed |= t
        return out, trimmed
    if isinstance(value, list):
        items = []
        for v in value[:keep]:
            nv, t = _trim_lists(v, keep)
            items.append(nv)
            trimmed |= t
        if len(value) > keep:
            items.append({"_truncated": f"{len(value) - keep} more items omitted"})
            trimmed = True
        return items, trimmed
    return value, False


def shape(result: Any, *, full: bool, max_bytes: int) -> dict[str, Any]:
    """Return an envelope: {"result", "truncated", "notes"}."""
    notes: set[str] = set()
    shaped = result if full else drop_bulky(result, notes)
    envelope: dict[str, Any] = {"truncated": False}
    if notes:
        envelope["omitted_fields"] = sorted(notes)

    if len(_dumps(shaped)) <= max_bytes:
        envelope["result"] = shaped
        return envelope

    for keep in (DEFAULT_LIST_KEEP, 10, 3):
        trimmed, _ = _trim_lists(shaped, keep)
        if len(_dumps(trimmed)) <= max_bytes:
            envelope.update(
                result=trimmed,
                truncated=True,
                truncation_note=f"Lists trimmed to {keep} items to fit {max_bytes} bytes. "
                "Narrow the query (e.g. smaller date range) for more.",
            )
            return envelope

    text = _dumps(shaped)
    envelope.update(
        result=None,
        truncated=True,
        partial_json=text[: max_bytes - 500],
        truncation_note=f"Response was {len(text)} bytes; showing the first "
        f"{max_bytes - 500} characters of raw JSON. Narrow the query.",
    )
    return envelope
