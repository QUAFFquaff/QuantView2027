from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from quantview.config import Settings
from quantview.data.repository import Repository
from quantview.ui.charts import main_history
from quantview.ui.queries import WorkbenchQueries, important_changes

APP = Path(__file__).resolve().parents[2] / "src/quantview/ui/app.py"


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    settings = Settings(root=tmp_path, app={"paths": {"database": "test.duckdb", "reports": "reports"}},
                        products={"LH": {"name": "生猪"}, "JD": {"name": "鸡蛋"}, "C": {"name": "玉米"}}, spreads={})
    repository = Repository(settings.database_path)
    repository.initialize()
    monkeypatch.setattr("quantview.config.load_settings", lambda *args: settings)
    with repository.connect() as connection:
        connection.execute("""
            INSERT INTO instrument (instrument_id, exchange, product, contract_code,
              delivery_year, delivery_month, is_active, source_symbol, updated_at)
            VALUES ('C2701', 'DCE', 'C', 'C2701', 2027, 1, true, 'C2701', now()),
                   ('C2705', 'DCE', 'C', 'C2705', 2027, 5, true, 'C2705', now())
        """)
        for day in ("2026-10-20", "2026-10-21"):
            for contract, price in (("C2701", 2300), ("C2705", 2400)):
                connection.execute("""
                    INSERT INTO futures_daily (trade_date, instrument_id, open, high, low,
                      close, settlement, volume, open_interest, source, source_fetched_at,
                      raw_batch_id, quality_status, quality_flags, settlement_return)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 100, 1000, 'fixture', now(), 'batch',
                      'warning', '["settlement_filled_from_close"]', 0.01)
                """, [day, contract, price, price+10, price-10, price, price])
            connection.execute("""
                INSERT INTO product_daily (trade_date, product, main_contract, second_contract,
                  main_settlement, main_changed, total_volume, total_open_interest)
                VALUES (?, 'C', 'C2701', 'C2705', 2300, false, 200, 2000)
            """, [day])
            connection.execute("""
                INSERT INTO calendar_spread_daily (trade_date, product, near_instrument_id,
                  far_instrument_id, near_contract, far_contract, near_delivery_ordinal,
                  far_delivery_ordinal, settlement_spread, pair_type, liquidity_status)
                VALUES (?, 'C', 'C2701', 'C2705', 'C2701', 'C2705', 24325, 24329,
                  -100, 'adjacent', 'ok')
            """, [day])
    return settings, repository


def test_main_history_breaks_every_roll_including_return_to_old_contract():
    frame = pd.DataFrame({"trade_date": pd.date_range("2026-10-01", periods=4),
                          "main_contract": ["A", "A", "B", "A"], "main_settlement": [1, 2, 20, 3]})
    figure = main_history(frame, "test", "元/吨")
    assert len(figure.data) == 3
    assert [list(trace.y) for trace in figure.data] == [[1, 2], [20], [3]]
    assert all(trace.connectgaps is False for trace in figure.data)


def test_query_dates_include_failures_and_never_read_future_bars(workbench):
    _, repository = workbench
    repository.start_run("failed", date(2026, 10, 22), "manual", "test")
    view = WorkbenchQueries(repository)
    assert view.dates()[0] == date(2026, 10, 22)
    assert len(view.bars("C", date(2026, 10, 20))) == 2
    assert view.products(date(2026, 10, 22)).empty


def test_old_ai_is_hidden_even_if_product_is_marked_completed(workbench):
    _, repository = workbench
    repository.start_run("new", date(2026, 10, 21), "manual", "test")
    repository.finish_run("new", status="success", stages={"ai": {"completed_products": ["C"]}},
                          rows_fetched=0, rows_accepted=0, rows_rejected=0, source_errors=[], report_path=None)
    with repository.connect() as connection:
        connection.execute("""
            INSERT INTO ai_analysis (analysis_id, trade_date, product, judge_view, created_at)
            VALUES ('old', '2026-10-21', 'C', 'STALE_ANALYSIS', '2026-10-20 12:00:00')
        """)
    view = WorkbenchQueries(repository)
    assert view.analysis(date(2026, 10, 21), {"ai": {"completed_products": ["C"]}}, "success").empty
    with repository.connect() as connection:
        connection.execute("UPDATE pipeline_run SET started_at='2026-10-21 11:00:00', finished_at='2026-10-21 13:00:00'")
        connection.execute("UPDATE ai_analysis SET created_at='2026-10-21 12:00:00'")
    assert len(view.analysis(date(2026, 10, 21), {"ai": {"completed_products": ["C"]}}, "success")) == 1
    assert view.analysis(date(2026, 10, 21), {}, "success").empty


def test_quality_events_precede_roll_events_and_are_deterministic(workbench):
    _, repository = workbench
    view = WorkbenchQueries(repository)
    products = view.products(date(2026, 10, 21))
    products.loc[0, "main_changed"] = True
    products.loc[0, "previous_main_contract"] = "C2705"
    bars = {p: view.bars(p, date(2026, 10, 21)) for p in ("LH", "JD", "C")}
    first = important_changes(products, {}, bars)
    second = important_changes(products, {}, dict(reversed(list(bars.items()))))
    assert first == second
    assert "覆盖" in first[0]
    assert "主力" in first[-1] and "C2705 → C2701" in first[-1]
    assert len(first) <= 5


def test_partial_dashboard_and_widgets_render_without_starting_pipeline(workbench, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Browsing must not run the pipeline")
    monkeypatch.setattr("quantview.ui.catch_up.DailyPipeline.run", forbidden)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    assert len(app.tabs) == 4
    assert app.selectbox(key="trade_date").value == date(2026, 10, 21)
    assert any(item.label == "替代结算（收盘价）" for item in app.metric)
    assert any("本轮没有可核验" in item.value for item in app.info)
    app.selectbox(key="product_detail").select("C").run()
    app.selectbox(key="product_curve").select("C").run()
    assert not app.exception
    assert len(app.get("plotly_chart")) >= 6
    app.selectbox(key="research_contract").select("C2705").run()
    assert not app.exception
    app.selectbox(key="trade_date").select(date(2026, 10, 20)).run()
    assert not app.exception
    assert app.selectbox(key="curve_comparison").value is None


def test_failed_only_date_keeps_all_tabs_and_error_evidence(workbench):
    _, repository = workbench
    repository.start_run("failure", date(2026, 10, 22), "manual", "test")
    repository.finish_run("failure", status="failed", stages={}, rows_fetched=0, rows_accepted=0,
                          rows_rejected=0, source_errors=[{"error": "fixture failure"}], report_path=None)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception
    assert app.selectbox(key="trade_date").value == date(2026, 10, 22)
    assert len(app.tabs) == 4
    assert any("没有品种指标" in item.value for item in app.warning)
    assert any("fixture failure" in item.value for item in app.json)
