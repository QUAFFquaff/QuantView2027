from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from quantview.config import Settings
from quantview.data.repository import Repository
from quantview.operations.calendar import import_calendar
from quantview.operations.clock import SHANGHAI

APP = Path(__file__).resolve().parents[2] / "src/quantview/ui/app.py"


@pytest.fixture
def ui_setup(tmp_path, monkeypatch):
    settings = Settings(
        root=tmp_path,
        app={
            "paths": {"database": "data/test.duckdb", "reports": "reports/daily"},
            "pipeline": {"scheduled_time": "18:10"},
        },
        products={"C": {}, "JD": {}, "LH": {}}, spreads={},
    )
    monkeypatch.setattr("quantview.config.load_settings", lambda *args: settings)
    instant = datetime(2026, 10, 26, 10, tzinfo=SHANGHAI)
    monkeypatch.setattr("quantview.operations.health.shanghai_now", lambda value=None: value or instant)
    monkeypatch.setattr("quantview.operations.calendar.shanghai_now", lambda value=None: value or instant)
    calls = []

    class Pipeline:
        def __init__(self, settings):
            pass

        def run(self, target, **kwargs):
            calls.append((target, kwargs))
            return {"status": "success", "rows_accepted": 6, "source_errors": []}

    monkeypatch.setattr("quantview.ui.catch_up.DailyPipeline", Pipeline)
    return settings, calls


def seed_calendar(settings):
    path = settings.root / "calendar.csv"
    path.write_text(
        "trade_date,is_trading_day\n2026-10-23,1\n2026-10-24,0\n2026-10-25,0\n2026-10-26,1\n",
        encoding="utf-8",
    )
    import_calendar(Repository(settings.database_path), path, source="synthetic UI fixture", source_version="v1", verified=True)


def test_empty_unverified_workbench_shows_status_and_disables_catch_up(ui_setup):
    settings, calls = ui_setup
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    assert app.button(key="catch_up_latest").disabled
    assert not calls
    assert not settings.database_path.exists()
    assert any("日历" in item.value for item in app.warning)


def test_empty_verified_workbench_only_runs_when_user_clicks_latest_missing(ui_setup):
    settings, calls = ui_setup
    seed_calendar(settings)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    assert not calls  # Opening and rendering the page never runs a pipeline.
    assert not app.button(key="catch_up_latest").disabled
    app.button(key="catch_up_latest").click().run()
    assert not app.exception
    assert calls == [(date(2026, 10, 23), {"trigger_type": "catch_up"})]
    app.run()
    assert len(calls) == 1


def test_ui_range_preflight_rejects_uncovered_dates_without_starting_pipeline(ui_setup):
    settings, calls = ui_setup
    seed_calendar(settings)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.date_input(key="backfill_range").set_value((date(2026, 10, 22), date(2026, 10, 23))).run()
    app.button(key="backfill_submit").click().run()
    assert not app.exception
    assert not calls
    assert any("未覆盖" in item.value for item in app.error)


def seed_current_with_old_audit_gaps(settings):
    import pandas as pd

    source = settings.root / "calendar.csv"
    source.write_text("trade_date,is_trading_day\n" + "".join(
        f"2026-10-{day},{int(day < 24 or day == 26)}\n" for day in range(20, 27)
    ), encoding="utf-8")
    repository = Repository(settings.database_path)
    import_calendar(repository, source, source="synthetic UI fixture", source_version="v1", verified=True)
    rows = [{"trade_date": date(2026, 10, day), "product": product, "main_contract": f"{product}2701", "main_settlement": 100}
            for day in range(20, 24) for product in settings.products]
    repository.replace_frame("product_daily", pd.DataFrame(rows), "trade_date", [date(2026, 10, day) for day in range(20, 24)])
    repository.start_run("latest", date(2026, 10, 23), "manual", "fixture")
    repository.finish_run("latest", status="success", stages={"calendar": {"status": "verified"},
                                                             "completeness": {"products": {p: {"status": "complete"} for p in settings.products}}},
                          rows_fetched=3, rows_accepted=3, rows_rejected=0, source_errors=[], report_path="fixture.md")


def test_latest_update_never_backtracks_into_older_unverified_history(ui_setup):
    settings, calls = ui_setup
    seed_current_with_old_audit_gaps(settings)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception and not calls
    assert any("最新已就绪行情已更新至 2026-10-23" in item.value for item in app.success)
    assert any("18:10" in item.value and "此前展示" in item.value for item in app.caption)
    assert "2026-10-23" in app.button(key="futures_update_latest").label
    assert "2026-10-22" in app.button(key="catch_up_latest").label
    assert not any("已发现 3 个缺失" in item.value for item in app.caption)
    app.button(key="futures_update_latest").click().run()
    assert not app.exception
    assert calls == [(date(2026, 10, 23), {"trigger_type": "catch_up", "allow_ai": False})]
    # Historical rebuild is still available explicitly and names its date.
    app.button(key="catch_up_latest").click().run()
    assert calls[-1][0] == date(2026, 10, 22)
    app.button(key="futures_update_latest").click().run()
    assert calls[-1][0] == date(2026, 10, 23)


def test_latest_update_selects_today_after_cutoff_even_with_historical_gaps(ui_setup, monkeypatch):
    settings, calls = ui_setup
    seed_current_with_old_audit_gaps(settings)
    instant = datetime(2026, 10, 26, 19, tzinfo=SHANGHAI)
    for module in ("quantview.operations.health", "quantview.operations.calendar"):
        monkeypatch.setattr(module + ".shanghai_now", lambda value=None: value or instant)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    assert not any("此前展示" in item.value for item in app.caption)
    app.button(key="futures_update_latest").click().run()
    assert calls == [(date(2026, 10, 26), {"trigger_type": "catch_up", "allow_ai": False})]


def test_latest_update_does_not_depend_on_older_calendar_coverage(ui_setup):
    settings, calls = ui_setup
    seed_current_with_old_audit_gaps(settings)
    with Repository(settings.database_path).connect() as connection:
        connection.execute("DELETE FROM exchange_calendar WHERE trade_date='2026-10-22'")
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    assert app.button(key="catch_up_latest").disabled  # Historical plan is unknown.
    assert not app.button(key="futures_update_latest").disabled
    app.button(key="futures_update_latest").click().run()
    assert calls == [(date(2026, 10, 23), {"trigger_type": "catch_up", "allow_ai": False})]


def test_manual_date_update_works_without_verified_calendar_only_on_click(ui_setup):
    settings, calls = ui_setup
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not calls
    app.date_input(key="manual_market_date").set_value(date(2026, 10, 23)).run()
    app.checkbox(key="manual_market_confirm").check().run()
    app.button(key="manual_market_submit").click().run()
    assert not app.exception
    assert calls == [(date(2026, 10, 23), {"trigger_type": "manual", "allow_ai": False})]
    app.run()
    assert len(calls) == 1
    assert not settings.database_path.exists()  # The fake pipeline performs no writes.


@pytest.mark.parametrize("target,message", [
    (date(2026, 10, 26), "尚未到"),
    (date(2026, 10, 25), "非交易日"),
])
def test_manual_update_rejects_premature_or_known_closed_dates(ui_setup, target, message):
    settings, calls = ui_setup
    seed_calendar(settings)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.date_input(key="manual_market_date").set_value(target).run()
    app.checkbox(key="manual_market_confirm").check().run()
    app.button(key="manual_market_submit").click().run()
    assert not app.exception
    assert not calls
    assert any(message in item.value for item in app.error)
