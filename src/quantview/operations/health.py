from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pandas as pd

from quantview.data.repository import Repository


def _business_days_between(latest: date, as_of: date) -> int:
    """Count weekdays after latest through as_of (exchange holidays are not inferred)."""
    if latest >= as_of:
        return 0
    current = latest + timedelta(days=1)
    count = 0
    while current <= as_of:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


def _record(frame: pd.DataFrame) -> dict[str, Any] | None:
    if frame.empty:
        return None
    return frame.iloc[0].to_dict()


def build_health_snapshot(
    repository: Repository,
    expected_products: list[str] | set[str] | tuple[str, ...],
    *,
    as_of: date | None = None,
    trial_days: int = 5,
) -> dict[str, Any]:
    """Build a JSON-serializable operational health view from persisted data."""
    as_of = as_of or date.today()
    expected = sorted(set(expected_products))
    latest_frame = repository.query("SELECT max(trade_date) AS latest_trade_date FROM product_daily")
    latest_value = latest_frame.iloc[0]["latest_trade_date"] if not latest_frame.empty else None
    latest_date = pd.to_datetime(latest_value).date() if pd.notna(latest_value) else None

    coverage: list[dict[str, Any]] = []
    if latest_date is not None:
        coverage_frame = repository.query(
            """
            SELECT product, main_contract, second_contract, main_changed
            FROM product_daily WHERE trade_date=? ORDER BY product
            """,
            [latest_date],
        )
        coverage = coverage_frame.to_dict("records")
    available = sorted({str(row["product"]) for row in coverage})
    missing = sorted(set(expected) - set(available))
    business_days_behind = (
        _business_days_between(latest_date, as_of) if latest_date is not None else None
    )

    latest_run = _record(
        repository.query(
            """
            SELECT target_trade_date, trigger_type, started_at, finished_at, status,
                   rows_fetched, rows_accepted, rows_rejected, source_errors, report_path
            FROM pipeline_run ORDER BY started_at DESC LIMIT 1
            """
        )
    )
    if latest_run is not None:
        for key in ("target_trade_date", "started_at", "finished_at"):
            value = latest_run.get(key)
            if value is None or pd.isna(value):
                latest_run[key] = None
            elif key == "target_trade_date":
                latest_run[key] = pd.to_datetime(value).date().isoformat()
            else:
                latest_run[key] = pd.to_datetime(value).isoformat()
        latest_run["source_errors"] = _json_value(latest_run.get("source_errors"), [])

    trial_frame = repository.query(
        """
        SELECT target_trade_date, status, report_path, rows_rejected, started_at
        FROM (
            SELECT *, row_number() OVER (
                PARTITION BY target_trade_date ORDER BY started_at DESC
            ) AS rn
            FROM pipeline_run WHERE target_trade_date <= ?
        ) WHERE rn=1
        ORDER BY target_trade_date DESC
        LIMIT ?
        """,
        [as_of, max(trial_days * 3, trial_days)],
    )
    successful_dates: list[str] = []
    for row in trial_frame.to_dict("records"):
        if row.get("status") != "success" or not row.get("report_path"):
            continue
        value = row["target_trade_date"]
        successful_dates.append(pd.to_datetime(value).date().isoformat())
        if len(successful_dates) == trial_days:
            break

    if latest_date is None:
        overall_status = "empty"
    elif missing:
        overall_status = "incomplete"
    elif latest_run is not None and latest_run.get("status") == "failed":
        overall_status = "failed"
    elif business_days_behind is not None and business_days_behind > 1:
        overall_status = "stale"
    elif business_days_behind == 1:
        overall_status = "due"
    else:
        overall_status = "healthy"

    return {
        "as_of": as_of.isoformat(),
        "overall_status": overall_status,
        "latest_market_date": latest_date.isoformat() if latest_date else None,
        "business_days_behind": business_days_behind,
        "coverage": {
            "expected": expected,
            "available": available,
            "missing": missing,
            "count": len(available),
            "expected_count": len(expected),
            "contracts": coverage,
        },
        "latest_run": latest_run,
        "trial": {
            "target_days": trial_days,
            "completed_days": len(successful_dates),
            "successful_dates": successful_dates,
            "passed": len(successful_dates) >= trial_days,
        },
        "calendar_note": "Freshness uses weekdays and does not exclude exchange holidays.",
    }
