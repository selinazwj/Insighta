"""Features / quality / serialization."""

from datetime import date
from datetime import datetime
from datetime import time
from datetime import timezone
from typing import Optional


def _normalize_excel_header(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _value_as_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_safe_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(v) for v in value]
    return value


def _latest_excel_quality_rows(rows: list) -> list:
    excel_rows = [row for row in rows if row.source_type == "excel"]
    other_rows = [row for row in rows if row.source_type != "excel"]
    if not excel_rows:
        return rows

    def _upload_key(row):
        ref = row.source_ref or ""
        return ref.rsplit(":row_", 1)[0] if ":row_" in ref else ref

    latest_row = max(excel_rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc))
    latest_key = _upload_key(latest_row)
    latest_excel = [row for row in excel_rows if _upload_key(row) == latest_key]
    latest_excel.sort(key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return latest_excel + other_rows


def _quality_row_label(index: int = 0) -> str:
    return str(index + 1)


__all__ = ['_normalize_excel_header', '_value_as_float', '_json_safe_value', '_latest_excel_quality_rows', '_quality_row_label']
