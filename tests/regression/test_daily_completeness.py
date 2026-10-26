from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from quantview.ai.provider import AIUnavailable
from quantview.config import Settings
from quantview.data.adapters import DataSourceError
from quantview.domain.models import FetchResult
from quantview.operations.calendar import import_calendar
from quantview.operations.clock import SHANGHAI
from quantview.operations.health import build_health_snapshot
from quantview.pipeline.daily import DailyPipeline

TARGET = date(2026, 10, 23)
ROOT = Path(__file__).resolve().parents[2]


def frame(*, target=TARGET, close=2200, **changes):
    row = {
        "date": target.isoformat(), "open": 2200, "high": 2230, "low": 2170,
        "close": close, "settle": 2200, "volume": 100, "hold": 1000,
    }
    row.update(changes)
    return pd.DataFrame([row])


class FakeAdapter:
    def __init__(self, name, contracts, responses):
        self.name = name
        self.contracts = contracts
        self.responses = responses
        self.calls = []

    def discover_contracts(self, product, product_name):
        return self.contracts[product]

    def fetch_daily(self, symbol, start, end):
        self.calls.append(symbol)
        response = self.responses.get(symbol)
        if response is None:
            raise DataSourceError("fixture source unavailable")
        return FetchResult(
            source=self.name, source_symbol=symbol, fetched_at=datetime.now().astimezone(),
            raw_batch_id=f"{self.name}-{symbol}-{len(self.calls)}", frame=response,
        )


@pytest.fixture
def pipeline(tmp_path):
    templates = Path("src/quantview/reports/templates")
    shutil.copytree(ROOT / templates, tmp_path / templates)
    contracts = {key: [f"{key}2701", f"{key}2703"] for key in ("C", "JD", "LH")}
    settings = Settings(
        root=tmp_path,
        app={
            "paths": {"database": "data/test.duckdb", "raw": "raw", "reports": "reports/daily"},
            "pipeline": {"lock_file": "data/pipeline.lock"},
            "data_sources": {"daily_priority": ["primary", "backup"], "save_raw_snapshots": False},
            "ai": {"enabled": False},
        },
        products={
            key: {"name": key, "exchange": "DCE", "allowed_delivery_months": [1, 3]}
            for key in contracts
        },
        spreads={},
    )
    pipeline = DailyPipeline(settings)
    primary = FakeAdapter(
        "primary", contracts,
        {symbol: frame() for symbols in contracts.values() for symbol in symbols},
    )
    backup = FakeAdapter("backup", contracts, {})
    pipeline.adapters = {"akshare_sina": primary, "primary": primary, "backup": backup}
    calendar_file = tmp_path / "calendar.csv"
    calendar_file.write_text("trade_date,is_trading_day\n2026-10-23,1\n", encoding="utf-8")
    import_calendar(
        pipeline.repository, calendar_file, source="synthetic regression fixture",
        source_version="v1", verified=True,
    )
    return pipeline


@pytest.mark.parametrize("primary_result", [
    None,
    frame(target=TARGET - timedelta(days=1)),
    frame(high=2100),
    frame(close=None),
])
def test_target_missing_or_invalid_uses_backup(pipeline, primary_result):
    primary, backup = pipeline.adapters["primary"], pipeline.adapters["backup"]
    primary.responses["C2703"] = primary_result
    backup.responses["C2703"] = frame()

    result = pipeline.run(TARGET)

    assert result["status"] == "success", result
    assert backup.calls == ["C2703"]
    target = pipeline.repository.query(
        "SELECT source FROM futures_daily WHERE trade_date=? AND instrument_id='DCE.C.2703'",
        [TARGET],
    )
    assert target.iloc[0]["source"] == "backup"
    assert result["source_errors"]


def test_backup_preserves_valid_primary_history_and_audits_raw_responses(pipeline):
    pipeline.settings.app["data_sources"]["save_raw_snapshots"] = True
    previous = TARGET - timedelta(days=1)
    pipeline.adapters["primary"].responses["C2703"] = frame(target=previous, close=2201)
    pipeline.adapters["backup"].responses["C2703"] = pd.concat(
        [frame(target=previous, close=2202), frame()], ignore_index=True
    )

    result = pipeline.run(TARGET)

    assert result["status"] == "success", result
    bars = pipeline.repository.query(
        "SELECT trade_date, source, close FROM futures_daily WHERE instrument_id='DCE.C.2703' ORDER BY trade_date"
    )
    assert bars["source"].tolist() == ["primary", "backup"]
    assert bars["close"].tolist() == [2201, 2200]
    assert len(list(pipeline.settings.path("raw").rglob("*.parquet"))) == 7


def test_partial_cross_section_is_not_success_even_with_all_three_products(pipeline):
    pipeline.adapters["primary"].responses["C2703"] = frame(high=2100)

    result = pipeline.run(TARGET)

    assert result["status"] == "partial", result
    assert result["stages"]["analytics"]["products"] == 3
    quality = result["stages"]["completeness"]["products"]["C"]
    assert quality["status"] == "partial"
    assert quality["missing_contracts"] == ["C2703"]
    assert result["rows_rejected"] == 1
    assert result["stages"]["ai"]["blocked_products"] == ["C"]
    content = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "C2703" in content
    assert "本次跳过 AI 分析" in content
    persisted = pipeline.repository.query("SELECT status, stages FROM pipeline_run")
    assert persisted.iloc[0]["status"] == "partial"
    assert json.loads(persisted.iloc[0]["stages"])["completeness"]["products"]["C"] == quality
    health = build_health_snapshot(pipeline.repository, pipeline.settings.products, as_of=TARGET)
    assert health["coverage"]["count"] == 3
    assert health["overall_status"] == "incomplete"


def test_rerun_reuses_valid_target_data_without_duplicates(pipeline):
    first = pipeline.run(TARGET)
    pipeline.adapters["primary"].responses["C2703"] = None
    second = pipeline.run(TARGET)

    assert first["status"] == second["status"] == "success", second
    assert second["stages"]["completeness"]["products"]["C"]["status"] == "complete"
    assert pipeline.repository.query("SELECT count(*) AS n FROM futures_daily").iloc[0]["n"] == 6
    assert pipeline.repository.query("SELECT count(*) AS n FROM pipeline_run").iloc[0]["n"] == 2


@pytest.mark.parametrize("second_run", ["gap", "unavailable", "failure", "disabled"])
def test_ai_gates_and_degradation_never_reuse_old_analysis(pipeline, monkeypatch, second_run):
    packs = []
    second_phase = False

    class FakeWorkflow:
        def __init__(self, *args, **kwargs):
            if second_phase and second_run == "unavailable":
                raise AIUnavailable("fixture has no AI key")

        def run(self, pack):
            packs.append(pack)
            if second_phase and second_run == "failure":
                raise RuntimeError("fixture AI timeout")
            product = pack["product"]
            return {
                "analysis_id": f"{product}-{len(packs)}", "trade_date": TARGET,
                "product": product, "bull_case": json.dumps({"thesis": "fixture bull"}),
                "bear_case": json.dumps({"thesis": "fixture bear"}),
                "judge_view": f"analysis-marker-{product}", "bias": "neutral", "confidence": 0.5,
                "key_evidence": [], "risk_flags": [], "invalidation_conditions": [],
                "created_at": datetime.now(),
            }

    monkeypatch.setattr("quantview.pipeline.daily.AIWorkflow", FakeWorkflow)
    pipeline.settings.app["ai"]["enabled"] = True
    first = pipeline.run(TARGET)
    assert first["status"] == "success", first
    assert {pack["product"] for pack in packs} == {"C", "JD", "LH"}
    assert "analysis-marker-C" in Path(first["report_path"]).read_text(encoding="utf-8")

    if second_run == "gap":
        # A newly discovered contract reveals a gap; keep old analyses for audit.
        pipeline.adapters["primary"].contracts["C"].append("C2705")
    elif second_run == "disabled":
        pipeline.settings.app["ai"]["enabled"] = False
    second_phase = True
    packs.clear()
    result = pipeline.run(TARGET)

    assert result["status"] == ("partial" if second_run == "gap" else "success"), result
    expected_called = {"JD", "LH"} if second_run == "gap" else (
        {"C", "JD", "LH"} if second_run == "failure" else set()
    )
    assert {pack["product"] for pack in packs} == expected_called
    assert all(pack["quality"]["status"] == "complete" for pack in packs)
    assert all(pack["quality"]["sources"] == ["primary"] for pack in packs)
    content = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "analysis-marker-C" not in content
    assert ("analysis-marker-JD" in content) == (second_run == "gap")
    assert not pipeline.repository.query("SELECT * FROM ai_analysis WHERE product='C'").empty


def test_settlement_substitution_is_visible_in_report_and_quality(pipeline):
    pipeline.adapters["primary"].responses["C2701"] = frame(settle=None)

    result = pipeline.run(TARGET)

    assert result["status"] == "success", result
    quality = result["stages"]["completeness"]["products"]["C"]
    assert quality["quality_warnings"] == [
        {"contract": "C2701", "flags": ["settlement_filled_from_close"]}
    ]
    assert "settlement_filled_from_close" in Path(result["report_path"]).read_text(encoding="utf-8")


def test_no_target_date_data_fails_and_blocks_ai(pipeline):
    pipeline.adapters["primary"].responses = {}

    result = pipeline.run(TARGET)

    assert result["status"] == "failed", result
    assert result["stages"]["ai"]["blocked_products"] == ["C", "JD", "LH"]
    assert result["stages"]["ai"]["completed_products"] == []


def test_failed_discovery_cannot_redefine_local_candidate_set_as_complete(pipeline, monkeypatch):
    assert pipeline.run(TARGET)["status"] == "success"

    def unavailable(*args):
        raise DataSourceError("fixture discovery unavailable")

    monkeypatch.setattr(pipeline.adapters["primary"], "discover_contracts", unavailable)
    result = pipeline.run(TARGET)

    assert result["status"] == "partial", result
    for quality in result["stages"]["completeness"]["products"].values():
        assert quality["missing_contracts"] == []
        assert quality["coverage_warnings"] == ["contract_universe_unverified"]
        assert quality["universe_source"] == "local_candidates"
    assert result["stages"]["ai"]["blocked_products"] == ["C", "JD", "LH"]
    assert "contract_universe_unverified" in Path(result["report_path"]).read_text(encoding="utf-8")


def test_unverified_manual_date_is_partial_and_blocks_ai(pipeline, monkeypatch):
    with pipeline.repository.connect() as connection:
        connection.execute("DELETE FROM exchange_calendar")

    def forbidden(*args):
        raise AssertionError("AI must not run without a verified calendar")

    monkeypatch.setattr(pipeline, "_run_ai", forbidden)
    result = pipeline.run(TARGET)
    assert result["status"] == "partial", result
    assert result["stages"]["calendar"]["status"] == "unknown"
    assert result["stages"]["ai"]["blocked_products"] == ["C", "JD", "LH"]
    assert "日历缺失或目标日期不在已核验覆盖范围内" in Path(result["report_path"]).read_text(encoding="utf-8")


@pytest.mark.parametrize(("target", "trigger", "now", "expected_status"), [
    (None, "scheduled", datetime(2026, 10, 26, 19, tzinfo=SHANGHAI), "failed"),
    (TARGET, "manual", datetime(2026, 10, 23, 10, tzinfo=SHANGHAI), "failed"),
    (date(2026, 10, 27), "manual", datetime(2026, 10, 26, 19, tzinfo=SHANGHAI), "failed"),
])
def test_blocked_target_does_not_fetch_or_create_run(pipeline, target, trigger, now, expected_status):
    result = pipeline.run(target, trigger_type=trigger, now=now)
    assert result["status"] == expected_status
    assert result["run_id"] is None
    assert not pipeline.adapters["primary"].calls
    assert pipeline.repository.query("SELECT * FROM pipeline_run").empty


def test_closed_day_is_skipped_without_fetch(pipeline):
    source = pipeline.settings.root / "calendar.csv"
    source.write_text("trade_date,is_trading_day\n2026-10-24,0\n", encoding="utf-8")
    import_calendar(pipeline.repository, source, source="fixture", source_version="closed", verified=True)
    result = pipeline.run(date(2026, 10, 24), now=datetime(2026, 10, 26, 19, tzinfo=SHANGHAI))
    assert result["status"] == "skipped"
    assert not pipeline.adapters["primary"].calls
    assert pipeline.repository.query("SELECT * FROM pipeline_run").empty


def test_default_target_uses_verified_previous_day_before_cutoff(pipeline):
    source = pipeline.settings.root / "calendar.csv"
    source.write_text(
        "trade_date,is_trading_day\n2026-10-23,1\n2026-10-24,0\n2026-10-25,0\n2026-10-26,1\n",
        encoding="utf-8",
    )
    import_calendar(pipeline.repository, source, source="fixture", source_version="weekend", verified=True)
    result = pipeline.run(now=datetime(2026, 10, 26, 10, tzinfo=SHANGHAI))
    assert result["status"] == "success", result
    assert result["target_trade_date"] == "2026-10-23"


def test_backfill_default_skips_ai_and_explicit_opt_in_is_honored(pipeline, monkeypatch):
    calls = []

    def spy(*args):
        calls.append(True)
        return []

    monkeypatch.setattr(pipeline, "_run_ai", spy)
    assert pipeline.run(TARGET, trigger_type="backfill")["status"] == "success"
    assert not calls
    assert pipeline.run(TARGET, trigger_type="backfill", allow_ai=True)["status"] == "success"
    assert calls == [True]
