from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from quantview.config import Settings
from quantview.metals.models import MetalsSettings
from quantview.metals.service import MetalsService

APP = Path(__file__).resolve().parents[2] / "src/quantview/ui/app.py"


def setup(tmp_path, monkeypatch):
    settings = Settings(root=tmp_path, app={"paths": {"database": "data/test.duckdb", "reports": "reports/daily"}, "pipeline": {"scheduled_time": "18:10"}}, products={"LH": {}, "JD": {}, "C": {}}, spreads={})
    monkeypatch.setattr("quantview.config.load_settings", lambda *args: settings)
    return settings


def test_navigation_empty_cache_and_saved_watchlist_never_fetch_on_render(tmp_path, monkeypatch):
    settings = setup(tmp_path, monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("page render must not make a network call")

    monkeypatch.setattr("quantview.metals.service.fetch_in_worker", forbidden)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.radio(key="workspace").set_value("贵金属").run()
    assert not app.exception
    assert any(item.value == "贵金属工作台" for item in app.title)
    assert not settings.database_path.exists()
    assert not (tmp_path / "data/market/metals").exists()
    app.text_area(key="metals_watchlist_text").set_value("161226,LOF,白银")
    app.button(key="metals_watchlist_save").click().run()
    assert not app.exception
    assert MetalsService(MetalsSettings(tmp_path)).watchlist()[0].kind == "LOF"
    app.run()
    assert not app.exception
    assert not settings.database_path.exists()


def test_explicit_update_shows_dated_quotes_and_failure_keeps_old_price(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    calls = []

    def fetch(asset, *args):
        calls.append(asset.symbol)
        return {"endpoint": "synthetic UI fixture", "verification": {"status": "benchmark", "name": asset.name}, "rows": [{"date": "2026-10-30", "close": 850 if asset.metal == "gold" else 12500}]}

    monkeypatch.setattr("quantview.metals.service.fetch_in_worker", fetch)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.radio(key="workspace").set_value("贵金属").run()
    assert not calls
    app.button(key="metals_sync").click().run()
    assert not app.exception
    assert calls == ["Au99.99", "Ag99.99"]
    assert any(item.value == "850.000" for item in app.metric)
    assert any(item.value == "68.00" for item in app.metric)
    app.run()
    assert len(calls) == 2

    def fail(*args):
        raise ValueError("synthetic outage")

    monkeypatch.setattr("quantview.metals.service.fetch_in_worker", fail)
    app.button(key="metals_sync").click().run()
    assert not app.exception
    assert any(item.value == "850.000" for item in app.metric)
    assert any("synthetic outage" in item.value for item in app.warning)


def test_fund_page_appends_without_overwriting_and_explicitly_updates_only_funds(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    service = MetalsService(MetalsSettings(tmp_path))
    service.save_watchlist([{"code": "518880", "kind": "ETF", "metal": "unknown"}])
    calls = []

    def fetch(asset, *args):
        calls.append(asset.symbol)
        return {
            "endpoint": "test fixture",
            "verification": {"status": "provider_category_verified", "category": asset.kind, "name": asset.symbol},
            "rows": [{"date": "2026-10-30", "close": 1.25}],
        }

    monkeypatch.setattr("quantview.metals.service.fetch_in_worker", fetch)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.radio(key="workspace").set_value("基金价格与暴露").run()
    assert not app.exception
    assert not calls
    app.button(key="funds_add_starters").click().run()
    assert not app.exception
    assert len(service.watchlist()) == 7
    assert service.watchlist()[0].metal == "unknown"
    assert not calls
    app.button(key="funds_sync").click().run()
    assert not app.exception
    assert calls == [asset.symbol for asset in service.watchlist()]
    assert all(service.latest(asset)["quotes"][-1]["close"] == 1.25 for asset in service.watchlist())
