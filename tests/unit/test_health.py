from datetime import date, datetime

import pandas as pd

from quantview.operations.health import build_health_snapshot


class StubRepository:
    def __init__(self, *, latest, coverage, latest_run, trial_runs):
        self.latest = latest
        self.coverage = coverage
        self.latest_run = latest_run
        self.trial_runs = trial_runs

    def query(self, sql, parameters=None):
        if "max(trade_date)" in sql:
            return pd.DataFrame([{"latest_trade_date": self.latest}])
        if "FROM product_daily WHERE trade_date" in sql:
            return pd.DataFrame(self.coverage)
        if "ORDER BY started_at DESC LIMIT 1" in sql:
            return pd.DataFrame([self.latest_run]) if self.latest_run else pd.DataFrame()
        if "PARTITION BY target_trade_date" in sql:
            return pd.DataFrame(self.trial_runs)
        raise AssertionError(sql)


def _run(target, status="success", report_path="report.md"):
    return {
        "target_trade_date": target,
        "trigger_type": "scheduled",
        "started_at": datetime(2026, 10, 21, 18, 10),
        "finished_at": datetime(2026, 10, 21, 18, 11),
        "status": status,
        "rows_fetched": 100,
        "rows_accepted": 100,
        "rows_rejected": 0,
        "source_errors": "[]",
        "report_path": report_path,
    }


def test_health_snapshot_reports_stale_and_trial_progress():
    repository = StubRepository(
        latest=date(2026, 10, 20),
        coverage=[
            {
                "product": product,
                "main_contract": f"{product}2701",
                "second_contract": None,
                "main_changed": False,
            }
            for product in ("C", "JD", "LH")
        ],
        latest_run=_run(date(2026, 10, 20)),
        trial_runs=[_run(date(2026, 10, 20)), _run(date(2026, 10, 19))],
    )

    result = build_health_snapshot(
        repository, {"LH", "JD", "C"}, as_of=date(2026, 10, 22), trial_days=5
    )

    assert result["overall_status"] == "stale"
    assert result["business_days_behind"] == 2
    assert result["coverage"]["missing"] == []
    assert result["trial"]["completed_days"] == 2
    assert result["trial"]["passed"] is False


def test_health_snapshot_prioritizes_missing_product():
    repository = StubRepository(
        latest=date(2026, 10, 22),
        coverage=[
            {
                "product": "C",
                "main_contract": "C2701",
                "second_contract": None,
                "main_changed": False,
            },
            {
                "product": "LH",
                "main_contract": "LH2701",
                "second_contract": None,
                "main_changed": False,
            },
        ],
        latest_run=_run(date(2026, 10, 22)),
        trial_runs=[],
    )

    result = build_health_snapshot(
        repository, {"LH", "JD", "C"}, as_of=date(2026, 10, 22), trial_days=5
    )

    assert result["overall_status"] == "incomplete"
    assert result["coverage"]["missing"] == ["JD"]
