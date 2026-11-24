from __future__ import annotations

import copy
import json
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from quantview.config import Settings
from quantview.funds.analytics import snapshot_difference
from quantview.funds.exposure import TEMPLATE, save_bundle
from quantview.metals.analytics import compare_prices, price_metrics, quote_frame
from quantview.metals.jobs import job_status, start_update, wait_update
from quantview.metals.models import BENCHMARKS, MetalsSettings
from quantview.metals.service import MetalsService, normalize
from quantview.operations.clock import SHANGHAI

NOW = datetime(2026, 11, 24, 12, tzinfo=SHANGHAI)


def snapshot(rows):
    return normalize({"endpoint": "test", "verification": {"status": "benchmark", "name": "test"}, "rows": rows}, BENCHMARKS["Au99.99"], NOW, 400)


def test_optional_ohlc_only_from_same_admitted_version_and_consistent_tuple():
    value = snapshot([
        {"date": "2026-11-20", "open": 9, "high": 11, "low": 8, "close": 10, "volume": 0},
        {"date": "2026-11-21", "open": 9, "high": 8, "low": 7, "close": 10, "volume": -1},
        {"date": "2026-11-22", "close": 10},
        {"date": "2026-11-23", "open": 10, "high": 12, "low": 9, "close": -1},
    ])
    before = copy.deepcopy(value)
    frame = quote_frame(value)
    assert len(frame) == 3 and frame.iloc[0]["open"] == 9
    assert frame.iloc[0]["volume"] == 0
    assert pd.isna(frame.iloc[1]["high"]) and pd.isna(frame.iloc[1]["volume"])
    assert pd.isna(frame.iloc[2]["open"])
    assert value == before  # Display must not rewrite a published version.
    value["raw_payload"]["rows"].append(value["raw_payload"]["rows"][0].copy())
    assert pd.isna(quote_frame(value).iloc[0]["open"])


def test_rolling_windows_and_drawdown_require_full_samples():
    dates = pd.date_range("2026-08-01", periods=61)
    values = [100.0] * 59 + [80.0, 90.0]
    frame = quote_frame(snapshot([{"date": day.date().isoformat(), "close": price} for day, price in zip(dates, values, strict=True)]))
    metrics = price_metrics(frame)
    assert metrics["change_1"] == pytest.approx(0.125)
    assert metrics["change_60"] == pytest.approx(-0.1)
    assert metrics["drawdown_60"] == pytest.approx(-0.2)
    assert metrics["volatility_20"] > 0
    assert frame["ma60"].iloc[:59].isna().all()
    short = price_metrics(frame.tail(20))
    assert short["change_20"] is None and short["drawdown_60"] is None
    assert short["volatility_20"] is None


def test_comparison_uses_exact_common_dates_and_shared_base_without_filling():
    a = pd.DataFrame({"trade_date": pd.to_datetime(["2026-11-19", "2026-11-20", "2026-11-23"]), "close": [10, 20, 30]})
    b = pd.DataFrame({"trade_date": pd.to_datetime(["2026-11-18", "2026-11-20", "2026-11-23"]), "close": [5, 10, 20]})
    indexed, correlation = compare_prices({"a": a, "b": b}, 60)
    assert list(indexed.index.strftime("%Y-%m-%d")) == ["2026-11-20", "2026-11-23"]
    assert indexed.iloc[0].tolist() == [100, 100]
    assert indexed.iloc[-1].tolist() == [150, 200]
    assert correlation.empty
    assert compare_prices({"a": a}, None)[0].empty


def test_background_job_is_nonblocking_unique_and_reports_partial_retry(tmp_path, monkeypatch):
    service = MetalsService(MetalsSettings(tmp_path))
    entered, release = threading.Event(), threading.Event()
    calls = []

    def fetch(asset, *args):
        calls.append(asset.symbol)
        if asset.symbol == "Au99.99":
            entered.set()
            assert release.wait(10)
        else:
            raise ValueError("test silver outage")
        return {"endpoint": "test", "verification": {"status": "benchmark", "name": asset.name}, "rows": [{"date": "2026-11-20", "close": 100}]}

    monkeypatch.setattr("quantview.metals.service.fetch_in_worker", fetch)
    try:
        start_update(service)
        assert entered.wait(3)
        assert job_status(tmp_path)["status"] == "running"
        with pytest.raises(ValueError, match="后台"):
            start_update(service)
        assert service.latest(BENCHMARKS["Au99.99"]) is None
    finally:
        release.set()
        wait_update(tmp_path)
    result = job_status(tmp_path)
    assert result["status"] == "partial" and len(result["results"]) == 2
    assert result["finished_at"] and "test silver outage" in result["results"][1]["error"]
    assert service.latest(BENCHMARKS["Au99.99"])["quotes"][-1]["close"] == 100
    start_update(service, asset_ids=[BENCHMARKS["Ag99.99"].asset_id])
    wait_update(tmp_path)
    assert calls == ["Au99.99", "Ag99.99", "Ag99.99"]
    assert len(job_status(tmp_path)["results"]) == 1


def test_background_exception_ends_job_and_unknown_retry_never_fetches(tmp_path, monkeypatch):
    service = MetalsService(MetalsSettings(tmp_path))
    with pytest.raises(ValueError, match="变化或为空"):
        start_update(service, asset_ids=["not_allowed"])
    monkeypatch.setattr(service, "sync", lambda **kwargs: (_ for _ in ()).throw(OSError("write rejected")))
    start_update(service)
    wait_update(tmp_path)
    assert job_status(tmp_path)["status"] == "failed"
    assert "write rejected" in job_status(tmp_path)["error"]


def test_snapshot_difference_distinguishes_listing_changes_and_reconciles():
    baseline = {"securities_value_cny": 1000, "positions": [{"code": "518880", "market_value_cny": 100}, {"code": "159920", "market_value_cny": 50}]}
    selected = {"securities_value_cny": 1100, "positions": [{"code": "518880", "market_value_cny": 150}, {"code": "512400", "market_value_cny": 200}]}
    result = snapshot_difference(selected, baseline)
    assert result["total_change"] == 100
    rows = {row["code"]: row for row in result["rows"]}
    assert rows["159920"]["change"] == -50 and rows["159920"]["state"] == "不再列示"
    assert rows["512400"]["state"] == "新增列示"
    assert sum(row["change"] for row in rows.values()) == 200


def test_terminal_ui_chart_controls_comparison_and_portfolio_reconcile(tmp_path, monkeypatch):
    settings = Settings(root=tmp_path, app={"paths": {"database": "data/test.duckdb", "reports": "reports/daily"},
                                            "pipeline": {"scheduled_time": "18:10"}},
                        products={"LH": {}, "JD": {}, "C": {}}, spreads={})
    monkeypatch.setattr("quantview.config.load_settings", lambda *args: settings)
    service = MetalsService(MetalsSettings(tmp_path))
    service.save_watchlist([{"code": code, "kind": "ETF", "metal": "unknown"} for code in ("518880", "159920")])

    def fetch(asset, *args):
        return {"endpoint": "test", "verification": {"status": "provider_category_verified", "category": "ETF", "name": asset.symbol},
                "rows": [{"date": day.date().isoformat(), "open": 9 + n, "close": 10 + n, "low": 8 + n, "high": 12 + n, "volume": n * 100}
                         for n, day in enumerate(pd.date_range("2026-08-01", periods=70))]}

    service.sync(fetcher=fetch, now=NOW, funds_only=True)
    bundle = copy.deepcopy(TEMPLATE)
    bundle.update(positions=[{"code": "518880", "market_value_cny": 400}, {"code": "159920", "market_value_cny": 200}])
    save_bundle(tmp_path, json.dumps(bundle).encode())
    monkeypatch.setattr("quantview.metals.service.fetch_in_worker", lambda *args: pytest.fail("render must stay offline"))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "src/quantview/ui/app.py"), default_timeout=30).run()
    app.radio(key="workspace").set_value("基金价格与暴露").run()
    app.selectbox(key="funds_chart_type").select("K 线").run()
    app.checkbox(key="funds_volume").check().run()
    assert not app.exception
    specs = [json.loads(item.proto.spec) for item in app.get("plotly_chart")]
    assert any(trace["type"] == "candlestick" for trace in specs[0]["data"])
    assert any(trace["type"] == "bar" and "成交量" in trace["name"] for trace in specs[0]["data"])
    assert len(specs[1]["data"]) == 2  # Two observed prices share a common base.
    pie = next(trace for spec in specs for trace in spec["data"] if trace["type"] == "pie")
    assert sum(pie["values"]) == 1000
    assert any(item.label == "最大已列单只权重" and item.value == "40.0%" for item in app.metric)
    app.text_input(key="funds_search").set_value("159920").run()
    assert not app.exception
    # Filtering the list does not silently change the selected detail symbol.
    assert app.selectbox(key="funds_asset").value.endswith("518880")
    service.append_watchlist([{"code": "512400", "kind": "ETF", "metal": "unknown"}])
    app.run()
    app.selectbox(key="funds_asset").select("SSE_512400").run()
    assert not app.exception
    assert any("512400 尚无已核验缓存" in item.value for item in app.info)


def test_running_update_disables_repeat_and_allows_workspace_navigation(tmp_path, monkeypatch):
    settings = Settings(root=tmp_path, app={"paths": {"database": "data/test.duckdb", "reports": "reports/daily"},
                                            "pipeline": {"scheduled_time": "18:10"}},
                        products={"LH": {}, "JD": {}, "C": {}}, spreads={})
    monkeypatch.setattr("quantview.config.load_settings", lambda *args: settings)
    entered, release = threading.Event(), threading.Event()

    def fetch(asset, *args):
        entered.set()
        assert release.wait(15)
        return {"endpoint": "test", "verification": {"status": "benchmark", "name": asset.name}, "rows": [{"date": "2026-11-20", "close": 100}]}

    monkeypatch.setattr("quantview.metals.service.fetch_in_worker", fetch)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "src/quantview/ui/app.py"), default_timeout=30).run()
    app.radio(key="workspace").set_value("贵金属").run()
    try:
        app.button(key="metals_sync").click().run()
        assert entered.wait(3)
        assert not app.exception and app.button(key="metals_sync").disabled
        app.radio(key="workspace").set_value("基金价格与暴露").run()
        assert not app.exception
        assert app.button(key="funds_sync").disabled
        assert app.text_input(key="fund_watch_code")
        assert job_status(tmp_path)["status"] == "running"
    finally:
        release.set()
        wait_update(tmp_path)
    app.run()
    assert not app.exception
    assert job_status(tmp_path)["status"] == "success"
