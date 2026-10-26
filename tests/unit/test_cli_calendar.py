from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd
import pytest

from quantview import cli
from quantview.config import Settings
from quantview.data.repository import Repository
from quantview.operations.calendar import import_calendar, natural_days
from quantview.operations.clock import SHANGHAI


@pytest.fixture
def setup_cli(tmp_path, monkeypatch):
    settings = Settings(
        root=tmp_path,
        app={"paths": {"database": "data/test.duckdb"}, "pipeline": {"scheduled_time": "18:10"}},
        products={"C": {}}, spreads={},
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "shanghai_now", lambda: datetime(2026, 10, 26, 19, tzinfo=SHANGHAI))
    repository = Repository(settings.database_path)
    source = tmp_path / "calendar.csv"
    source.write_text("trade_date,is_trading_day\n" + "".join(
        f"{target},{int(target.weekday() < 5 and target != date(2026, 10, 22))}\n"
        for target in natural_days(date(2026, 10, 21), date(2026, 10, 26))
    ), encoding="utf-8")
    import_calendar(repository, source, source="synthetic fixture", source_version="v1", verified=True)
    calls = []

    class Pipeline:
        def __init__(self, settings):
            pass

        def run(self, target=None, **kwargs):
            calls.append((target, kwargs))
            return {"status": "success", "target_trade_date": str(target)}

    monkeypatch.setattr(cli, "DailyPipeline", Pipeline)
    return settings, repository, calls


def test_parser_does_not_capture_host_local_today():
    assert cli.build_parser().parse_args(["run-daily"]).date is None
    assert cli.build_parser().parse_args(["status"]).date is None
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["import-calendar", "calendar.csv", "--source", "fixture", "--version", "v1"])


@pytest.mark.parametrize("with_ai", [False, True])
def test_backfill_only_runs_verified_days_and_explicit_ai_policy(setup_cli, with_ai):
    _, _, calls = setup_cli
    args = ["backfill", "--start", "2026-10-21", "--end", "2026-10-26"]
    assert cli.main(args + (["--with-ai"] if with_ai else [])) == 0
    assert [target for target, _ in calls] == [date(2026, 10, 21), date(2026, 10, 23), date(2026, 10, 26)]
    assert all(kwargs["allow_ai"] is with_ai for _, kwargs in calls)


def test_outside_calendar_range_rejects_entire_backfill_before_any_call(setup_cli, capsys):
    _, _, calls = setup_cli
    assert cli.main(["backfill", "--start", "2026-10-21", "--end", "2026-10-27"]) == 2
    assert not calls
    assert "未覆盖" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("all_days", [False, True])
def test_catch_up_defaults_to_latest_missing_day(setup_cli, all_days):
    _, repository, calls = setup_cli
    target = date(2026, 10, 21)
    repository.replace_frame(
        "product_daily", pd.DataFrame([{"trade_date": target, "product": "C"}]), "trade_date", [target],
    )
    repository.start_run("fixture", target, "manual", "fixture")
    repository.finish_run(
        "fixture", status="success", stages={"calendar": {"status": "verified"}},
        rows_fetched=1, rows_accepted=1, rows_rejected=0, source_errors=[], report_path="fixture.md",
    )
    # Health uses the same explicitly injected Shanghai clock.
    monkey_now = datetime(2026, 10, 26, 19, tzinfo=SHANGHAI)
    from unittest.mock import patch
    with patch("quantview.operations.health.shanghai_now", return_value=monkey_now):
        assert cli.main(["catch-up"] + (["--all"] if all_days else [])) == 0
    assert [target for target, _ in calls] == (
        [date(2026, 10, 23), date(2026, 10, 26)] if all_days else [date(2026, 10, 26)]
    )


def test_run_daily_preserves_automatic_selection_and_explicit_manual_override(setup_cli):
    _, _, calls = setup_cli
    assert cli.main(["run-daily"]) == 0
    assert calls[-1] == (None, {"trigger_type": "manual", "allow_ai": None})
    assert cli.main(["run-daily", "--date", "2026-10-23", "--no-ai"]) == 0
    assert calls[-1] == (date(2026, 10, 23), {"trigger_type": "manual", "allow_ai": False})
