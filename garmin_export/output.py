"""Plain-text document with raw JSON blocks, plus compact-mode helpers.

No markdown and no code fences on purpose: NotebookLM's RAG indexer has
bugs with .md files and skips content inside code fences.
"""

import json
from typing import Any

TIMESERIES_MAX_POINTS = 24

# Daily-health keys known to contain high-frequency arrays
DAILY_TIMESERIES_KEYS = {"heart_rate", "stress", "sleep", "respiration",
                         "hrv", "body_battery", "bb_events", "intensity_min"}


class Document:
    """Accumulates the export text. Formatting depends on compact/split mode."""

    def __init__(self, compact: bool = False, split: bool = False):
        self.compact = compact or split
        self.split = split
        self.lines: list[str] = []

    def append(self, text: str) -> None:
        self.lines.append(text)

    def json(self, data) -> str:
        """Serialize to JSON. Compact mode is single-line and strips empties.

        In split mode, top-level dicts/lists get one entry per line so text
        parsers (like NotebookLM) can index the content.
        """
        if self.compact:
            data = strip_empty(data)
        if self.split:
            if isinstance(data, dict) and data:
                lines = [f"  {_dumps(k)}: {_dumps(v)}" for k, v in data.items()]
                return "{\n" + ",\n".join(lines) + "\n}"
            if isinstance(data, list) and data:
                return "[\n" + ",\n".join(f"  {_dumps(item)}" for item in data) + "\n]"
        return json.dumps(data, indent=None if self.compact else 2, default=str, ensure_ascii=False)

    def section(self, title: str, data) -> None:
        """Append a titled JSON block. Skips if data is None."""
        if data is None:
            return
        self.append(f"{title}\n")
        self.append(f"{self.json(data)}\n")

    def nodata(self) -> None:
        """Note for a section with nothing to export."""
        self.append("No data available.\n")

    def text(self) -> str:
        return "\n".join(self.lines)


def _dumps(value) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def word_count(text: str) -> int:
    """Fast approximate word count."""
    return len(text.split())


def strip_empty(data):
    """Recursively remove None, empty strings, empty lists, and empty dicts."""
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            v = strip_empty(v)
            if v is None or v == "" or v == [] or v == {}:
                continue
            cleaned[k] = v
        return cleaned
    if isinstance(data, list):
        return [c for c in (strip_empty(item) for item in data) if c is not None]
    return data


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def downsample_timeseries(data, max_points: int = TIMESERIES_MAX_POINTS):
    """Reduce a high-frequency time series to about max_points buckets.

    Garmin returns two formats: list-of-dicts (``[{"timestamp": ..., ...}]``)
    and list-of-lists (``[[timestamp, value, ...], ...]``). Both are handled:
    each bucket keeps its first row (timestamps/labels) with numeric fields
    averaged across the bucket.
    """
    if not isinstance(data, list) or len(data) <= max_points:
        return data
    bucket_size = -(-len(data) // max_points)  # ceil: never more than max_points buckets

    if isinstance(data[0], (list, tuple)):
        result = []
        for i in range(0, len(data), bucket_size):
            bucket = data[i:i + bucket_size]
            merged: Any = list(bucket[0])  # keep first row's timestamp
            for col in range(1, len(merged)):
                if _is_number(merged[col]):
                    vals = [row[col] for row in bucket if len(row) > col and _is_number(row[col])]
                    if vals:
                        merged[col] = round(sum(vals) / len(vals), 1)
            result.append(merged)
        return result

    if not isinstance(data[0], dict):
        return data

    result = []
    for i in range(0, len(data), bucket_size):
        bucket = data[i:i + bucket_size]
        merged = dict(bucket[0])  # keep first row's timestamps/labels
        for k in merged:
            if _is_number(merged[k]):
                vals = [row[k] for row in bucket if _is_number(row.get(k))]
                if vals:
                    merged[k] = round(sum(vals) / len(vals), 1)
        result.append(merged)
    return result


def compact_daily(data):
    """Downsample one day's high-frequency series (HR, stress, sleep...) to ~hourly."""
    if not isinstance(data, dict):
        return data
    compacted: dict[str, Any] = {}
    for k, v in data.items():
        if k in DAILY_TIMESERIES_KEYS and isinstance(v, dict):
            # Often a dict with nested lists: downsample the inner lists
            compacted[k] = _downsample_lists(v)
        elif k in DAILY_TIMESERIES_KEYS and isinstance(v, list):
            if v and all(isinstance(item, dict) and any(isinstance(iv, list) for iv in item.values())
                         for item in v):
                # List of objects holding the series (body battery: [{..., "bodyBatteryValuesArray": [...]}])
                compacted[k] = [_downsample_lists(item) for item in v]
            else:
                compacted[k] = downsample_timeseries(v)
        else:
            compacted[k] = v
    return compacted


def _downsample_lists(obj: dict) -> dict:
    return {k: downsample_timeseries(v) if isinstance(v, list) else v for k, v in obj.items()}
