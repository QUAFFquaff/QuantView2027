from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any

import pandas as pd

from quantview.data.repository import Repository
from quantview.operations.calendar import CalendarUnavailable, TradingCalendar, load_calendar
from quantview.operations.clock import SHANGHAI, shanghai_now


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


def _run_record(frame: pd.DataFrame) -> dict | None:
    if frame.empty:
        return None
    row = frame.iloc[0].to_dict()
    for key in ("target_trade_date", "started_at", "finished_at"):
        value = row.get(key)
        row[key] = None if value is None or pd.isna(value) else pd.to_datetime(value).isoformat()
    row["source_errors"] = _json_value(row.get("source_errors"), [])
    row["stages"] = _json_value(row.get("stages"), {})
    return row


def _ran_on_target_day(row: dict, target: date) -> bool:
    if row.get("trigger_type") == "backfill":
        return False
    stamp = _json_value(row.get("stages"), {}).get("calendar", {}).get("as_of")
    try:
        instant = datetime.fromisoformat(stamp)
        return instant.tzinfo is not None and shanghai_now(instant).date() == target
    except (TypeError, ValueError):
        return False


def build_health_snapshot(
    repository: Repository,
    expected_products: list[str] | set[str] | tuple[str, ...],
    *,
    as_of: date | None = None,
    now: datetime | None = None,
    calendar: TradingCalendar | None = None,
    ready_time: time = time(18, 10),
    trial_days: int = 5,
) -> dict[str, Any]:
    """Read-only health and catch-up planning using the verified calendar.

    An explicit as_of date is evaluated at the daily collection cutoff. Without
    as_of, the current Shanghai instant determines whether today's run is due.
    No network or pipeline work is initiated here.
    """
    if trial_days < 1:
        raise ValueError("trial_days must be positive")
    instant = datetime.combine(as_of, ready_time, SHANGHAI) if as_of else shanghai_now(now)
    calendar = calendar or load_calendar(repository)
    calendar_state = calendar.describe(instant.date())
    try:
        expected_date = calendar.expected_trade_date(now=instant, ready_time=ready_time)
    except CalendarUnavailable as exc:
        expected_date = None
        calendar_state.update(status="unknown", reason=str(exc))
    calendar_state["expected_trade_date"] = expected_date.isoformat() if expected_date else None
    calendar_state["as_of"] = instant.isoformat()
    calendar_state["ready_time"] = ready_time.isoformat(timespec="minutes")

    expected = sorted(set(expected_products))
    exists = repository.path.exists()
    if exists:
        tables = repository.query("SELECT table_name FROM information_schema.tables WHERE table_schema='main'")
        exists = "product_daily" in set(tables["table_name"])
    target_limit = expected_date or instant.date()
    market_dates = repository.query(
        "SELECT DISTINCT trade_date FROM product_daily WHERE trade_date<=? ORDER BY trade_date", [target_limit]
    ) if exists else pd.DataFrame()
    latest_date = pd.to_datetime(market_dates.iloc[-1]["trade_date"]).date() if not market_dates.empty else None
    coverage_frame = repository.query(
        "SELECT product, main_contract, second_contract, main_changed FROM product_daily WHERE trade_date=? ORDER BY product",
        [latest_date],
    ) if latest_date else pd.DataFrame()
    coverage = coverage_frame.to_dict("records")
    available = sorted({str(row["product"]) for row in coverage})
    missing = sorted(set(expected) - set(available))
    latest_run = _run_record(repository.query(
        "SELECT * FROM pipeline_run ORDER BY started_at DESC LIMIT 1"
    )) if exists else None
    # Historical failures remain visible but do not replace the current day's status.
    current_run = _run_record(repository.query(
        "SELECT * FROM pipeline_run WHERE target_trade_date=? ORDER BY started_at DESC LIMIT 1",
        [expected_date],
    )) if exists and expected_date else None
    runs = repository.query(
        """
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY target_trade_date ORDER BY started_at DESC
            ) AS rn FROM pipeline_run WHERE target_trade_date<=?
        ) WHERE rn=1 ORDER BY target_trade_date
        """,
        [target_limit],
    ) if exists else pd.DataFrame()
    by_date = {pd.to_datetime(row["target_trade_date"]).date(): row for row in runs.to_dict("records")}
    complete_dates = {
        target for target, row in by_date.items()
        if row["status"] == "success" and row.get("report_path")
        and _json_value(row.get("stages"), {}).get("calendar", {}).get("status") == "verified"
    }
    latest_complete = max(complete_dates) if complete_dates else None
    days_behind = None
    missing_dates: list[str] = []
    planning_note = None
    if expected_date:
        try:
            if latest_date and latest_date <= expected_date:
                days_behind = len(calendar.trading_days(latest_date, expected_date)) - 1
            # On an empty database, offer only the most recent missing day.
            observed = [pd.to_datetime(value).date() for value in market_dates.get("trade_date", [])]
            observed.extend(target for target in by_date if target <= expected_date)
            start = max(min(observed), min(calendar.days)) if observed else expected_date
            missing_dates = [day.isoformat() for day in calendar.trading_days(start, expected_date) if day not in complete_dates]
        except CalendarUnavailable as exc:
            planning_note = str(exc)

    successful_dates: list[str] = []
    if expected_date:
        for target in sorted((day for day, flag in calendar.days.items() if flag and day <= expected_date), reverse=True):
            if (
                not calendar.covers(target, expected_date) or target not in complete_dates
                or not _ran_on_target_day(by_date[target], target)
            ):
                break
            successful_dates.append(target.isoformat())
            if len(successful_dates) >= trial_days:
                break

    if latest_date is None:
        overall = "empty"
    elif missing:
        overall = "incomplete"
    elif current_run and current_run["status"] == "failed":
        overall = "failed"
    elif current_run and current_run["status"] == "partial":
        overall = "incomplete"
    elif expected_date is None:
        overall = "calendar_unknown"
    elif days_behind is None:
        overall = "calendar_unknown"
    elif days_behind > 1:
        overall = "stale"
    elif days_behind == 1:
        overall = "due"
    elif expected_date not in complete_dates:
        overall = "incomplete"
    else:
        overall = "healthy"
    return {
        "as_of": instant.date().isoformat(), "overall_status": overall,
        "latest_market_date": latest_date.isoformat() if latest_date else None,
        "latest_complete_date": latest_complete.isoformat() if latest_complete else None,
        "expected_trade_date": expected_date.isoformat() if expected_date else None,
        "trading_days_behind": days_behind,
        "business_days_behind": days_behind,  # Backward-compatible field; now exchange days.
        "coverage": {"expected": expected, "available": available, "missing": missing,
                     "count": len(available), "expected_count": len(expected), "contracts": coverage},
        "latest_run": latest_run, "current_run": current_run, "calendar": calendar_state,
        "catch_up": {"missing_dates": missing_dates, "count": len(missing_dates),
                     "latest_missing_date": missing_dates[-1] if missing_dates else None,
                     "available": expected_date is not None and planning_note is None,
                     "reason": planning_note or calendar_state.get("reason")},
        "trial": {"target_days": trial_days, "completed_days": len(successful_dates),
                  "successful_dates": successful_dates, "passed": len(successful_dates) >= trial_days,
                  "note": "按已核验日历连续计数，排除历史回填及事后生成；尚不代表快照、自动运行及真实行情全部验收通过"},
        "calendar_note": "新鲜度按已核验交易日历计算；未知日期不回退到工作日估算。",
    }
