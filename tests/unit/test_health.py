from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd
import pytest

from quantview.data.repository import Repository
from quantview.operations.calendar import TradingCalendar, natural_days
from quantview.operations.clock import SHANGHAI
from quantview.operations.health import build_health_snapshot

PRODUCTS = {"C", "JD", "LH"}


@pytest.fixture
def calendar():
    return TradingCalendar(days={
        day: day.weekday() < 5
        for day in natural_days(date(2026, 10, 19), date(2026, 10, 26))
    })


@pytest.fixture
def repository(tmp_path):
    repository = Repository(tmp_path / "health.duckdb")
    repository.initialize()
    return repository


def seed(repository, target, *, status="success", products=PRODUCTS, verified=True):
    frame = pd.DataFrame([
        {"trade_date": target, "product": product, "main_contract": f"{product}2701"}
        for product in sorted(products)
    ])
    repository.replace_frame("product_daily", frame, "trade_date", [target])
    run_id = f"{target}-{status}"
    repository.start_run(run_id, target, "scheduled", "fixture")
    repository.finish_run(
        run_id, status=status,
        stages={"calendar": {
            "status": "verified" if verified else "unknown",
            "as_of": datetime.combine(target, time(18, 10), SHANGHAI).isoformat(),
        }},
        rows_fetched=100, rows_accepted=100, rows_rejected=0,
        source_errors=[], report_path="fixture-report.md",
    )


def test_health_snapshot_reports_real_trading_day_lag(repository, calendar):
    seed(repository, date(2026, 10, 19))
    seed(repository, date(2026, 10, 20))
    result = build_health_snapshot(repository, PRODUCTS, as_of=date(2026, 10, 22), calendar=calendar)
    assert result["overall_status"] == "stale"
    assert result["trading_days_behind"] == 2
    assert result["coverage"]["missing"] == []
    assert result["catch_up"]["missing_dates"] == ["2026-10-21", "2026-10-22"]
    assert result["trial"]["completed_days"] == 0
    assert not result["trial"]["passed"]


def test_health_snapshot_prioritizes_missing_product(repository, calendar):
    seed(repository, date(2026, 10, 22), status="partial", products={"C", "LH"})
    result = build_health_snapshot(repository, PRODUCTS, as_of=date(2026, 10, 22), calendar=calendar)
    assert result["overall_status"] == "incomplete"
    assert result["coverage"]["missing"] == ["JD"]


def test_monday_before_cutoff_and_weekend_do_not_make_friday_stale(repository, calendar):
    seed(repository, date(2026, 10, 23))
    for instant in (datetime(2026, 10, 25, 19, tzinfo=SHANGHAI), datetime(2026, 10, 26, 10, tzinfo=SHANGHAI)):
        result = build_health_snapshot(repository, PRODUCTS, now=instant, calendar=calendar)
        assert result["expected_trade_date"] == "2026-10-23"
        assert result["overall_status"] == "healthy"
        assert result["trading_days_behind"] == 0
        assert result["catch_up"]["missing_dates"] == []


def test_unverified_calendar_never_claims_freshness_or_trial_pass(repository):
    seed(repository, date(2026, 10, 23), verified=False)
    result = build_health_snapshot(repository, PRODUCTS, as_of=date(2026, 10, 26))
    assert result["overall_status"] == "calendar_unknown"
    assert result["trading_days_behind"] is None
    assert result["expected_trade_date"] is None
    assert not result["catch_up"]["available"]
    assert not result["trial"]["passed"]


def test_historical_failure_does_not_replace_current_health(repository, calendar):
    seed(repository, date(2026, 10, 23))
    seed(repository, date(2026, 10, 20), status="failed")
    result = build_health_snapshot(repository, PRODUCTS, as_of=date(2026, 10, 23), calendar=calendar)
    assert result["latest_run"]["status"] == "failed"
    assert result["current_run"]["status"] == "success"
    assert result["overall_status"] == "healthy"


def test_trial_requires_consecutive_verified_dates_ending_at_expected_day(repository, calendar):
    for target in (19, 20, 21, 22, 23):
        seed(repository, date(2026, 10, target), status="failed" if target == 21 else "success")
    result = build_health_snapshot(repository, PRODUCTS, as_of=date(2026, 10, 23), calendar=calendar)
    assert result["trial"]["successful_dates"] == ["2026-10-23", "2026-10-22"]
    assert not result["trial"]["passed"]
    # A successful rerun repairs that day without deleting its failure audit.
    seed(repository, date(2026, 10, 21))
    result = build_health_snapshot(repository, PRODUCTS, as_of=date(2026, 10, 23), calendar=calendar)
    assert result["trial"]["completed_days"] == 5
    assert result["trial"]["passed"]


def test_empty_database_offers_only_latest_missing_day_and_does_not_write(tmp_path, calendar):
    repository = Repository(tmp_path / "not-created.duckdb")
    result = build_health_snapshot(repository, PRODUCTS, as_of=date(2026, 10, 26), calendar=calendar)
    assert result["overall_status"] == "empty"
    assert result["catch_up"]["missing_dates"] == ["2026-10-26"]
    assert not repository.path.exists()


def test_historical_backfill_restores_data_without_passing_daily_trial(repository, calendar):
    for target in (19, 20, 21, 22, 23):
        seed(repository, date(2026, 10, target))
    with repository.connect() as connection:
        connection.execute("UPDATE pipeline_run SET trigger_type='backfill'")
    result = build_health_snapshot(repository, PRODUCTS, as_of=date(2026, 10, 23), calendar=calendar)
    assert result["overall_status"] == "healthy"
    assert result["trial"]["completed_days"] == 0
    assert not result["trial"]["passed"]
