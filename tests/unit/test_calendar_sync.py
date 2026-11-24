from __future__ import annotations

import copy
import csv
import io
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from quantview import cli
from quantview.config import Settings
from quantview.data.repository import Repository
from quantview.operations.calendar import load_calendar
from quantview.operations.calendar_sync import (
    HOLIDAYS,
    normalize_calendar,
    read_backup,
    sync_calendar,
)
from quantview.operations.clock import SHANGHAI

NOW = datetime(2026, 11, 24, 14, tzinfo=SHANGHAI)
APP = Path(__file__).resolve().parents[2] / "src/quantview/ui/app.py"


@pytest.fixture
def settings(tmp_path):
    return Settings(root=tmp_path, app={"paths": {"database": "data/test.duckdb", "reports": "reports/daily"},
                                        "pipeline": {"scheduled_time": "18:10"}}, products={"LH": {}, "JD": {}, "C": {}}, spreads={})


@pytest.fixture
def payload():
    # Public 2026 date facts, restated as a deterministic offline fixture.
    spans = [
        ("new-year", "01-01", "01-03", "01-01", "01-04", "01-05"),
        ("spring-festival", "02-15", "02-23", "02-14", "02-23", "02-24"),
        ("qingming", "04-04", "04-06", "04-04", "04-06", "04-07"),
        ("labour-day", "05-01", "05-05", "05-01", "05-05", "05-06"),
        ("dragon-boat", "06-19", "06-21", "06-19", "06-21", "06-22"),
        ("mid-autumn", "09-25", "09-27", "09-25", "09-27", "09-28"),
        ("national-day", "10-01", "10-07", "10-01", "10-07", "10-08"),
    ]
    return {"year": 2026, "license": "CC-BY-4.0", "data_version": "fixture-2026-v1",
            "attribution": "Markets Open (marketsopen.today), CC BY 4.0; offline test fixture",
            "rows": [{"market_id": "DCE", "time_zone": "Asia/Shanghai", "source_type": "official", "holiday_id": identity,
                      "holiday_start": "2026-" + start, "holiday_end": "2026-" + end,
                      "closed_from": "2026-" + first, "closed_to": "2026-" + last, "reopens_on": "2026-" + reopen,
                      "official_notice_url": "https://www.dce.com.cn/dce/content/2025/ywggytz/18625615.html"}
                     for identity, start, end, first, last, reopen in spans]}


def encoded(payload):
    return json.dumps(payload, ensure_ascii=False).encode()


def test_annual_calendar_accounts_for_exchange_weekends_and_holidays(payload):
    content, metadata = normalize_calendar(encoded(payload), 2026)
    days = {row["trade_date"]: row["is_trading_day"] for row in csv.DictReader(io.StringIO(content.decode()))}
    assert len(days) == 365 and metadata["trading_days"] == 242
    for day in ("2026-01-04", "2026-02-14", "2026-02-23", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-07", "2026-10-10"):
        assert days[day] == "0"  # Make-up workdays are not exchange trading days.
    assert days["2026-02-24"] == days["2026-10-08"] == days["2026-11-24"] == "1"
    assert len(metadata["source_sha256"]) == len(metadata["csv_sha256"]) == 64


def test_sync_keeps_immutable_raw_csv_provenance_and_reuses_backup_offline(settings, payload):
    raw, calls = encoded(payload), []

    def fetch(year):
        calls.append(year)
        return raw

    first = sync_calendar(settings, fetcher=fetch, now=NOW)
    folder = Path(first["backup_directory"])
    assert (folder / "source.json").read_bytes() == raw
    assert (folder / "calendar.csv").read_bytes() == read_backup(settings, 2026)[0]
    assert first["status"] == "synced" and first["database_changed"]
    calendar = load_calendar(Repository(settings.database_path))
    assert calendar.expected_trade_date(now=NOW) == date(2026, 11, 23)
    assert calendar.provenance[date(2026, 11, 24)]["file_hash"] == first["csv_sha256"]
    assert sync_calendar(settings, fetcher=fetch, now=NOW)["status"] == "cached"
    assert calls == [2026]
    assert sync_calendar(settings, fetcher=fetch, now=NOW, refresh=True)["fetched_at"] == first["fetched_at"]
    assert len(Repository(settings.database_path).query("select * from calendar_import")) == 1
    assert len(list(folder.parent.iterdir())) == 1
    settings.database_path.unlink()
    restored = sync_calendar(settings, now=NOW, offline=True, fetcher=lambda _: pytest.fail("offline must not fetch"))
    assert restored["status"] == "restored" and restored["database_changed"]
    assert load_calendar(Repository(settings.database_path)).days[date(2026, 11, 24)]


@pytest.mark.parametrize("change", ["year", "market", "incomplete", "duplicate", "timezone", "notice", "reopen", "extended_weekday", "license"])
def test_unavailable_or_invalid_sources_never_replace_existing_calendar(settings, payload, change):
    first = sync_calendar(settings, fetcher=lambda _: encoded(payload), now=NOW)
    before = load_calendar(Repository(settings.database_path)).days
    broken = copy.deepcopy(payload)
    if change == "year":
        broken["year"] = 2027
    elif change == "market":
        for row in broken["rows"]:
            row["market_id"] = "SSE"
    elif change == "incomplete":
        broken["rows"].pop()
    elif change == "duplicate":
        broken["rows"].append(broken["rows"][0])
    elif change == "timezone":
        broken["rows"][0]["time_zone"] = "UTC"
    elif change == "notice":
        broken["rows"][0]["official_notice_url"] = "https://example.com/not-an-exchange"
    elif change == "reopen":
        broken["rows"][0]["reopens_on"] = "2026-01-06"
    elif change == "extended_weekday":
        broken["rows"][0].update(closed_to="2026-01-05", reopens_on="2026-01-06")
    else:
        broken["license"] = "unknown"
    with pytest.raises(ValueError):
        sync_calendar(settings, fetcher=lambda _: encoded(broken), now=NOW, refresh=True)
    assert load_calendar(Repository(settings.database_path)).days == before
    assert read_backup(settings, 2026)[1]["source_sha256"] == first["source_sha256"]
    assert not (settings.root / "data/reference/calendars/sync.lock").exists()


def test_new_source_version_is_archived_with_fresh_database_provenance(settings, payload):
    first = sync_calendar(settings, fetcher=lambda _: encoded(payload), now=NOW)
    payload["data_version"] = "fixture-2026-v2"
    second = sync_calendar(settings, fetcher=lambda _: encoded(payload), now=NOW, refresh=True)
    assert first["source_sha256"] != second["source_sha256"]
    assert len(list(Path(first["backup_directory"]).parent.iterdir())) == 2
    assert (Path(first["backup_directory"]) / "source.json").exists()
    assert load_calendar(Repository(settings.database_path)).describe(date(2026, 11, 24))["source_version"].startswith("fixture-2026-v2")


def test_network_outage_corrupt_backup_and_missing_year_are_explicit(settings, payload):
    first = sync_calendar(settings, fetcher=lambda _: encoded(payload), now=NOW)

    def fail(_):
        raise ValueError("synthetic download outage")

    with pytest.raises(ValueError, match="outage"):
        sync_calendar(settings, now=NOW, refresh=True, fetcher=fail)
    assert read_backup(settings, 2026)
    with pytest.raises(ValueError, match="没有 2027"):
        sync_calendar(settings, year=2027, now=NOW, offline=True, fetcher=lambda _: pytest.fail("must not fetch"))
    folder = Path(first["backup_directory"])
    (folder / "calendar.csv").write_text("corrupt")
    with pytest.raises(ValueError, match="校验失败"):
        sync_calendar(settings, now=NOW, offline=True)
    assert len(load_calendar(Repository(settings.database_path)).days) == 365


def test_cli_supports_low_frequency_sync_and_offline_restore(settings, payload, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    calls = []
    monkeypatch.setattr("quantview.operations.calendar_sync.download_calendar", lambda year: (calls.append(year), encoded(payload))[1])
    assert cli.main(["sync-calendar", "--year", "2026"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "synced"
    assert cli.main(["sync-calendar", "--year", "2026", "--offline"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "cached"
    assert calls == [2026]
    assert cli.main(["sync-calendar", "--year", "2027", "--offline"]) == 2


def test_ui_only_syncs_on_click_and_month_grid_shows_verified_boundaries(settings, payload, monkeypatch):
    monkeypatch.setattr("quantview.config.load_settings", lambda *args: settings)
    for module in ("quantview.operations.health", "quantview.operations.calendar", "quantview.operations.calendar_sync"):
        monkeypatch.setattr(module + ".shanghai_now", lambda value=None: value or NOW)
    calls = []
    monkeypatch.setattr("quantview.operations.calendar_sync.download_calendar", lambda year: (calls.append(year), encoded(payload))[1])
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not app.exception and not calls and not settings.database_path.exists()
    app.button(key="calendar_sync_submit").click().run()
    assert not app.exception and calls == [2026]
    assert not app.button(key="futures_update_latest").disabled
    assert any("242 个交易日" in item.value for item in app.success)
    app.selectbox(key="calendar_view_month").select(10).run()
    html = "".join(item.value for item in app.markdown)
    assert 'class="closed"><strong>1</strong><span>休市' in html
    assert 'class="open"><strong>8</strong><span>开市' in html
    app.number_input(key="calendar_view_year").set_value(2027).run()
    assert any("所选年度未完整覆盖" in item.value for item in app.info)
    app.button(key="calendar_sync_submit").click().run()
    assert calls == [2026]  # Reuse the local public backup, not another HTTP request.
    assert HOLIDAYS <= {row["holiday_id"] for row in payload["rows"]}
