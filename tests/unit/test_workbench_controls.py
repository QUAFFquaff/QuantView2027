from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from quantview.config import Settings
from quantview.funds.exposure import bundle_history, load_bundle
from quantview.funds.imports import CODE, VALUE
from quantview.metals.models import MetalsSettings
from quantview.metals.service import MetalsService
from quantview.operations.clock import SHANGHAI

APP = Path(__file__).resolve().parents[2] / "src/quantview/ui/app.py"
NOW = datetime(2026, 11, 24, 12, tzinfo=SHANGHAI)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = Settings(root=tmp_path, app={"paths": {"database": "data/test.duckdb", "reports": "reports/daily"},
                                            "pipeline": {"scheduled_time": "18:10"}},
                        products={"LH": {}, "JD": {}, "C": {}}, spreads={})
    monkeypatch.setattr("quantview.config.load_settings", lambda *args: settings)
    for module in ("quantview.operations.health", "quantview.operations.calendar", "quantview.ui.fund_import",
                   "quantview.funds.exposure", "quantview.ui.data_status"):
        monkeypatch.setattr(module + ".shanghai_now", lambda value=None: value or NOW)

    def forbidden(*args, **kwargs):
        raise AssertionError("Browsing must not fetch market data")
    monkeypatch.setattr("quantview.metals.service.fetch_in_worker", forbidden)
    return settings


def fund_app():
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.radio(key="workspace").set_value("基金价格与暴露").run()
    assert not app.exception
    return app


def test_watchlist_add_update_and_remove_in_fund_page(setup):
    app = fund_app()
    service = MetalsService(MetalsSettings(setup.root))
    app.text_input(key="fund_watch_code").set_value("518880")
    app.button(key="fund_watch_add").click().run()
    assert not app.exception
    assert service.watchlist()[0].metal == "unknown"
    app.selectbox(key="fund_watch_metal").select("黄金")
    app.button(key="fund_watch_add").click().run()
    assert not app.exception
    assert len(service.watchlist()) == 1 and service.watchlist()[0].metal == "gold"
    app.text_input(key="fund_watch_code").set_value("bad")
    app.button(key="fund_watch_add").click().run()
    assert not app.exception
    assert len(service.watchlist()) == 1
    assert app.text_input(key="fund_watch_code").value == "bad"
    app.button(key="fund_watch_remove").click().run()
    assert not app.exception
    assert not service.watchlist()
    assert not setup.database_path.exists()


def fill_snapshot(app, amount="100"):
    app.session_state["fund_position_seed"] = pd.DataFrame([{CODE: "518880", VALUE: amount}])
    app.session_state["fund_editor_generation"] = 1
    app.number_input(key="fund_snapshot_total").set_value(1000.0).run()
    app.button(key="fund_preview_table").click().run()
    return app


def test_preview_does_not_write_confirmation_saves_and_edits_invalidate(setup):
    app = fill_snapshot(fund_app())
    assert not app.exception
    assert load_bundle(setup.root) is None
    assert not (setup.root / "data/portfolio").exists()
    assert app.button(key="fund_confirm_snapshot")
    app.text_input(key="fund_snapshot_source").set_value("changed source").run()
    assert not any(button.key == "fund_confirm_snapshot" for button in app.button)
    app.button(key="fund_preview_table").click().run()
    app.button(key="fund_confirm_snapshot").click().run()
    assert not app.exception
    assert load_bundle(setup.root)["positions"][0]["market_value_cny"] == 100
    assert len(bundle_history(setup.root)) == 1
    app.run()
    assert len(bundle_history(setup.root)) == 1


def test_invalid_table_keeps_input_and_does_not_save(setup):
    app = fill_snapshot(fund_app(), amount="-1")
    assert not app.exception
    assert any("正数" in error.value for error in app.error)
    assert load_bundle(setup.root) is None
    assert not any(button.key == "fund_confirm_snapshot" for button in app.button)


def test_manual_collection_requires_confirmation_and_rejects_unready_day(setup, monkeypatch):
    calls = []

    class Pipeline:
        def __init__(self, *args):
            pass

        def run(self, target, **kwargs):
            calls.append((target, kwargs))
            return {"status": "partial", "rows_accepted": 6, "source_errors": []}

    monkeypatch.setattr("quantview.ui.catch_up.DailyPipeline", Pipeline)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert app.button(key="manual_market_submit").disabled
    app.checkbox(key="manual_market_confirm").check().run()
    app.date_input(key="manual_market_date").set_value(date(2026, 11, 24)).run()
    app.button(key="manual_market_submit").click().run()
    assert not app.exception
    assert not calls
    assert any("尚未到" in error.value for error in app.error)
    app.date_input(key="manual_market_date").set_value(date(2026, 11, 23)).run()
    app.button(key="manual_market_submit").click().run()
    assert not app.exception
    assert calls == [(date(2026, 11, 23), {"trigger_type": "manual", "allow_ai": False})]
    app.run()
    assert len(calls) == 1


def test_manual_collection_exception_is_visible_and_does_not_repeat(setup, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic lock conflict")
    monkeypatch.setattr("quantview.ui.catch_up.DailyPipeline.run", fail)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.checkbox(key="manual_market_confirm").check().run()
    app.button(key="manual_market_submit").click().run()
    assert not app.exception
    assert any("最近采集未成功" in error.value for error in app.error)
    assert any("synthetic lock conflict" in item.value for item in app.json)
