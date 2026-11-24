from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from quantview.metals.fetch import fetch_in_worker, fetch_quotes
from quantview.metals.models import BENCHMARKS, MetalsSettings, fund_asset, load_metals_settings
from quantview.metals.service import MetalsService, gold_silver_ratio, indicators, normalize
from quantview.operations.clock import SHANGHAI
from quantview.ui.metals import parse_watchlist

NOW = datetime(2026, 11, 2, 18, 30, tzinfo=SHANGHAI)
GOLD = BENCHMARKS["Au99.99"]
SILVER = BENCHMARKS["Ag99.99"]


def payload(prices=(800, 850), *, asset=GOLD):
    end = date(2026, 10, 30)
    return {"endpoint": "synthetic_fixture", "verification": {"status": "benchmark", "name": asset.name}, "rows": [{"date": (end - timedelta(days=len(prices) - index - 1)).isoformat(), "close": price} for index, price in enumerate(prices)]}


def snapshot(prices=(800, 850), *, asset=GOLD):
    return normalize(payload(prices, asset=asset), asset, NOW, 400)


def test_dates_units_available_time_and_warmup_are_explicit():
    result = snapshot()
    assert result["asset"]["price_unit"] == "元/克"
    assert result["observed_at"] is None
    assert result["timestamp_precision"] == "date"
    assert result["available_at"] == NOW.isoformat()
    assert result["quality"]["calendar_coverage"] == "unverified"
    assert indicators(result)["change_5"] is None
    assert indicators(result)["drawdown_60"] is None


def test_returns_use_required_observations_and_drawdown_uses_running_peak():
    result = snapshot([100] * 58 + [200, 150])
    values = indicators(result)
    assert values["change_5"] == pytest.approx(.5)
    assert values["change_20"] == pytest.approx(.5)
    assert values["drawdown_60"] == pytest.approx(-.25)
    assert indicators(snapshot([100] * 5 + [120]))["change_5"] == pytest.approx(.2)


def test_ratio_converts_grams_and_refuses_mismatched_dates_or_basis():
    gold, silver = snapshot([850]), snapshot([12500], asset=SILVER)
    ratio, note = gold_silver_ratio(gold, silver)
    assert ratio == pytest.approx(68)
    assert "1000" in note
    silver["quotes"][-1]["trade_date"] = "2026-10-29"
    assert gold_silver_ratio(gold, silver)[0] is None
    silver = snapshot([12500], asset=SILVER)
    silver["asset"]["price_unit"] = "元/克"
    assert gold_silver_ratio(gold, silver)[0] is None
    assert gold_silver_ratio(None, silver)[0] is None


@pytest.mark.parametrize("price", [None, 0, -1, float("nan"), float("inf"), True, "850"])
def test_bad_prices_are_counted_and_never_filled(price):
    result = snapshot([800, price])
    assert len(result["quotes"]) == 1
    assert result["quality"]["rejected_rows"] == [{"trade_date": "2026-10-30", "reason": "invalid_close"}]


def test_future_conflicting_and_empty_batches_are_rejected():
    raw = payload()
    raw["rows"][-1]["date"] = "2026-11-03"
    with pytest.raises(ValueError, match="未来"):
        normalize(raw, GOLD, NOW, 400)
    raw = payload()
    raw["rows"].append({"date": "2026-10-30", "close": 999})
    with pytest.raises(ValueError, match="冲突"):
        normalize(raw, GOLD, NOW, 400)
    with pytest.raises(ValueError, match="有效"):
        snapshot([None])


def test_today_is_deferred_before_cutoff_and_not_mislabeled_as_close():
    raw = payload()
    raw["rows"].append({"date": "2026-11-02", "close": 999})
    before = normalize(raw, GOLD, NOW.replace(hour=12), 400)
    assert before["quotes"][-1]["trade_date"] == "2026-10-30"
    assert before["quality"]["deferred_unready_dates"] == ["2026-11-02"]
    after = normalize(raw, GOLD, NOW.replace(hour=18, minute=10), 400)
    assert after["quotes"][-1]["trade_date"] == "2026-11-02"


def test_failed_sync_retains_exact_successful_version_and_records_failure(tmp_path):
    service = MetalsService(MetalsSettings(tmp_path))
    assert not service.root.exists()
    assert service.status()["assets"][0]["latest_date"] is None
    assert not service.root.exists()  # A read never initializes or downloads data.
    service.sync(fetcher=lambda *args: payload(), now=NOW)
    first = service.latest(GOLD)
    assert len(list((service.root / GOLD.asset_id / "versions").glob("*.json"))) == 1

    def failing(*args):
        raise ValueError("fixture outage")

    result = service.sync(fetcher=failing, now=NOW)
    assert result["status"] == "failed"
    assert service.latest(GOLD) == first
    assert service.attempt(GOLD)["status"] == "failed"
    service.sync(fetcher=lambda *args: payload([870]), now=NOW)
    assert service.latest(GOLD)["version"] != first["version"]
    assert len(list((service.root / GOLD.asset_id / "versions").glob("*.json"))) == 2


def test_lock_blocks_writer_without_damaging_cache(tmp_path):
    service = MetalsService(MetalsSettings(tmp_path))
    service.root.mkdir(parents=True)
    (service.root / "write.lock").write_text("another writer")
    calls = []
    with pytest.raises(ValueError, match="正在更新"):
        service.sync(fetcher=lambda *args: calls.append(args), now=NOW)
    with pytest.raises(ValueError, match="正在更新"):
        service.save_watchlist([])
    assert not calls
    assert (service.root / "write.lock").read_text() == "another writer"


def test_lock_permission_error_gives_restart_hint_without_fetching(tmp_path, monkeypatch):
    import quantview.metals.service as storage

    service = MetalsService(MetalsSettings(tmp_path))
    service.root.mkdir(parents=True)
    original_open = storage.os.open
    calls = []

    def denied(path, *args, **kwargs):
        if str(path).endswith("write.lock"):
            raise PermissionError(13, "fixture permission denied", str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(storage.os, "open", denied)
    with pytest.raises(ValueError, match="正常 Windows PowerShell"):
        service.sync(fetcher=lambda *args: calls.append(args), now=NOW)
    assert not calls
    assert not (service.root / "write.lock").exists()
    assert not list(service.root.glob("*/versions/*.json"))


def test_lock_permission_error_does_not_remove_an_existing_lock(tmp_path, monkeypatch):
    import quantview.metals.service as storage

    service = MetalsService(MetalsSettings(tmp_path))
    service.root.mkdir(parents=True)
    lock = service.root / "write.lock"
    lock.write_text("another writer")

    def denied(*args, **kwargs):
        raise PermissionError(13, "fixture permission denied")

    monkeypatch.setattr(storage.os, "open", denied)
    with pytest.raises(ValueError, match="写入权限"):
        service.save_watchlist([])
    assert lock.read_text() == "another writer"


def test_changed_snapshot_fails_hash_validation(tmp_path):
    service = MetalsService(MetalsSettings(tmp_path))
    service.sync(fetcher=lambda *args: payload(), now=NOW)
    original = service.latest(GOLD)
    path = service.root / GOLD.asset_id / "versions" / f"{original['version']}.json"
    original["quotes"][-1]["close"] = 999999
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="校验失败"):
        service.latest(GOLD)


def test_watchlist_validates_duplicates_and_never_reuses_a_different_category(tmp_path):
    service = MetalsService(MetalsSettings(tmp_path))
    rows = parse_watchlist("518880，ETF，黄金\n161226,lof,白银")
    service.save_watchlist(rows)
    assert [a.kind for a in service.watchlist()] == ["ETF", "LOF"]
    assert service.latest(service.watchlist()[0]) is None
    with pytest.raises(ValueError, match="重复"):
        service.save_watchlist([rows[0], {**rows[0], "kind": "LOF"}])
    assert len(service.watchlist()) == 2
    with pytest.raises(ValueError):
        parse_watchlist("518880,白银")
    with pytest.raises(ValueError):
        fund_asset("../../etc", "ETF", "gold")


def test_fund_adapter_verifies_etf_and_lof_category_before_loading(monkeypatch):
    calls = []

    def directory(symbol):
        calls.append(symbol)
        return pd.DataFrame([{"代码": "sz161226", "名称": "synthetic LOF"}])

    fake = SimpleNamespace(fund_etf_category_sina=directory, fund_etf_hist_sina=lambda symbol: pd.DataFrame([{"date": "2026-10-30", "close": 1.5}]))
    monkeypatch.setitem(sys.modules, "akshare", fake)
    asset = fund_asset("161226", "LOF", "silver")
    result = fetch_quotes(asset, 400, NOW.date())
    assert calls == ["LOF基金"]
    assert normalize(result, asset, NOW, 400)["verification"]["underlying_status"] == "unverified"
    wrong = copy.deepcopy(result)
    wrong["verification"]["category"] = "ETF"
    with pytest.raises(ValueError, match="类型"):
        normalize(wrong, asset, NOW, 400)
    with pytest.raises(ValueError, match="确认"):
        fetch_quotes(fund_asset("518880", "ETF", "gold"), 400, NOW.date())


def test_worker_timeout_and_provider_error_are_actionable(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("worker", 5)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ValueError, match="超过 5 秒"):
        fetch_in_worker(GOLD, 400, NOW.date(), 5)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='progress\nQV_METAL:{"error":"fixture error"}\n'))
    with pytest.raises(ValueError, match="fixture error"):
        fetch_in_worker(GOLD, 400, NOW.date(), 5)


def test_configuration_defaults_and_validation(tmp_path):
    assert load_metals_settings(tmp_path).benchmarks == ("Au99.99", "Ag99.99")
    folder = tmp_path / "config"
    folder.mkdir()
    path = folder / "metals.yaml"
    for text in ["benchmarks: [AU]\n", "timeout_seconds: 0\n", "unknown: 1\n", "- invalid\n"]:
        path.write_text(text)
        with pytest.raises(ValueError):
            load_metals_settings(tmp_path)


def test_cli_status_is_read_only_and_independent_of_dce(tmp_path, monkeypatch, capsys):
    from quantview.cli import main

    monkeypatch.setattr("quantview.cli.load_settings", lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr("quantview.cli.load_calendar", lambda *args: pytest.fail("metals must not load DCE calendar"))
    assert main(["metals", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["calendar_coverage"] == "unverified"
    assert not (tmp_path / "data").exists()
