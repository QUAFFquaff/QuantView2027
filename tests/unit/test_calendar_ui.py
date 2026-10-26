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
