from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, time

import pytest

from quantview.config import Settings, load_settings
from quantview.data.repository import Repository
from quantview.operations.calendar import (
    CalendarUnavailable,
    TradingCalendar,
    backfill_targets,
    import_calendar,
    load_calendar,
    natural_days,
    resolve_target,
)
from quantview.operations.clock import SHANGHAI, shanghai_now


@pytest.fixture
def calendar():
    days = {day: day.weekday() < 5 for day in natural_days(date(2026, 10, 19), date(2026, 10, 27))}
    # A synthetic weekday closure; this fixture is not an actual exchange calendar.
    days[date(2026, 10, 22)] = False
    return TradingCalendar(days=days)


@pytest.mark.parametrize(("instant", "expected"), [
    ("2026-10-21T18:09:59+08:00", "2026-10-20"),
    ("2026-10-21T18:10:00+08:00", "2026-10-21"),
    ("2026-10-22T18:30:00+08:00", "2026-10-21"),
    ("2026-10-24T18:30:00+08:00", "2026-10-23"),
    ("2026-10-25T09:00:00+08:00", "2026-10-23"),
    ("2026-10-26T09:00:00+08:00", "2026-10-23"),
    ("2026-10-26T18:10:00+08:00", "2026-10-26"),
    # Host is still on Sunday; the application is already in Monday Shanghai time.
    ("2026-10-25T20:00:00-07:00", "2026-10-23"),
    ("2026-10-26T10:10:00+00:00", "2026-10-26"),
])
def test_target_selection_uses_shanghai_cutoff_and_explicit_calendar(calendar, instant, expected):
    assert calendar.expected_trade_date(now=datetime.fromisoformat(instant)) == date.fromisoformat(expected)


def test_no_weekday_fallback_or_gaps(calendar):
    with pytest.raises(CalendarUnavailable):
        TradingCalendar().expected_trade_date(now=datetime(2026, 10, 26, 19, tzinfo=SHANGHAI))
    with pytest.raises(CalendarUnavailable):
        calendar.expected_trade_date(now=datetime(2026, 10, 28, 19, tzinfo=SHANGHAI))
    del calendar.days[date(2026, 10, 25)]
    with pytest.raises(CalendarUnavailable, match="缺口"):
        calendar.expected_trade_date(now=datetime(2026, 10, 26, 10, tzinfo=SHANGHAI))
    with pytest.raises(CalendarUnavailable):
        calendar.trading_days(date(2026, 10, 23), date(2026, 10, 26))


def test_cutoff_is_configurable_and_naive_instants_are_rejected(calendar):
    instant = datetime(2026, 10, 26, 18, 10, tzinfo=SHANGHAI)
    assert calendar.expected_trade_date(now=instant, ready_time=time(19)) == date(2026, 10, 23)
    with pytest.raises(ValueError, match="timezone-aware"):
        shanghai_now(datetime(2026, 10, 26))
    assert shanghai_now(datetime(2026, 10, 25, 20, tzinfo=UTC)).date() == date(2026, 10, 26)


def test_manual_unknown_date_is_warned_but_automatic_work_is_blocked():
    now = datetime(2026, 10, 26, 19, tzinfo=SHANGHAI)
    calendar = TradingCalendar()
    manual = resolve_target(calendar, date(2026, 10, 23), now=now)
    assert manual["allowed"] and manual["status"] == "unknown"
    assert manual["reason"]
    assert not resolve_target(calendar, None, now=now)["allowed"]
    for trigger in ("scheduled", "catch_up"):
        assert not resolve_target(calendar, date(2026, 10, 23), trigger_type=trigger, now=now)["allowed"]


def test_explicit_closed_premature_and_future_targets_do_not_run(calendar):
    now = datetime(2026, 10, 26, 10, tzinfo=SHANGHAI)
    closed = resolve_target(calendar, date(2026, 10, 22), now=now)
    assert closed["skipped"] and not closed["allowed"]
    for target in (date(2026, 10, 26), date(2026, 10, 27)):
        result = resolve_target(calendar, target, now=now)
        assert result["status"] == "blocked" and not result["allowed"]


def test_backfill_preflights_the_entire_range(calendar):
    now = datetime(2026, 10, 26, 19, tzinfo=SHANGHAI)
    assert backfill_targets(calendar, date(2026, 10, 21), date(2026, 10, 26), now=now) == [
        date(2026, 10, 21), date(2026, 10, 23), date(2026, 10, 26),
    ]
    with pytest.raises(CalendarUnavailable):
        backfill_targets(calendar, date(2026, 10, 19), date(2026, 10, 28), now=now)
    with pytest.raises(CalendarUnavailable, match="未来"):
        backfill_targets(calendar, date(2026, 10, 23), date(2026, 10, 27), now=now)
    with pytest.raises(CalendarUnavailable, match="尚未到"):
        backfill_targets(calendar, date(2026, 10, 23), date(2026, 10, 26), now=now.replace(hour=10))


def test_import_records_provenance_and_retains_previous_import_audit(tmp_path):
    repository = Repository(tmp_path / "calendar.duckdb")
    path = tmp_path / "verified.csv"
    path.write_text("trade_date,is_trading_day\n2026-10-23,1\n2026-10-24,0\n", encoding="utf-8")
    first = import_calendar(repository, path, source="synthetic fixture", source_version="v1", verified=True)
    calendar = load_calendar(repository)
    assert calendar.days == {date(2026, 10, 23): True, date(2026, 10, 24): False}
    description = calendar.describe(date(2026, 10, 23))
    assert description["source"] == "synthetic fixture"
    assert description["source_version"] == "v1"
    assert len(description["file_hash"]) == 64
    assert description["verified_at"]
    path.write_text("trade_date,is_trading_day\n2026-10-24,true\n", encoding="utf-8")
    second = import_calendar(repository, path, source="synthetic correction", source_version="v2", verified=True)
    assert first["import_id"] != second["import_id"]
    assert len(repository.query("SELECT * FROM calendar_import")) == 2
    assert len(repository.query("SELECT * FROM exchange_calendar")) == 2
    corrected = load_calendar(repository)
    assert corrected.days[date(2026, 10, 24)] is True  # No implicit weekend override.
    assert corrected.describe(date(2026, 10, 23))["source_version"] == "v1"


@pytest.mark.parametrize("content", [
    "trade_date,is_trading_day\n",
    "date,is_trading_day\n2026-10-23,1\n",
    "trade_date,is_trading_day\n2026-10-23,yes\n",
    "trade_date,is_trading_day\n2026-10-23,1\n2026-10-23,0\n",
    "trade_date,is_trading_day\n2026-10-23,1\n2026-10-25,0\n",
    "trade_date,is_trading_day\n2026-02-30,1\n",
    "trade_date,is_trading_day\n20261023,1\n",
    "trade_date,is_trading_day\n2026-10-23,1,extra\n",
])
def test_invalid_import_does_not_create_or_modify_database(tmp_path, content):
    repository = Repository(tmp_path / "calendar.duckdb")
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        import_calendar(repository, path, source="fixture", source_version="v1", verified=True)
    assert not repository.path.exists()


def test_verification_is_explicit_and_calendar_reads_are_side_effect_free(tmp_path):
    repository = Repository(tmp_path / "calendar.duckdb")
    assert not load_calendar(repository).days
    assert not repository.path.exists()
    with pytest.raises(ValueError, match="核验"):
        import_calendar(repository, tmp_path / "missing.csv", source="fixture", source_version="v1")


def test_failed_replacement_rolls_back_calendar_and_import_audit(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "calendar.duckdb")
    path = tmp_path / "calendar.csv"
    path.write_text("trade_date,is_trading_day\n2026-10-24,0\n", encoding="utf-8")
    import_calendar(repository, path, source="fixture", source_version="v1", verified=True)
    real_connect = repository.connect

    class FailingInsert:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, *args, **kwargs):
            return self.connection.execute(*args, **kwargs)

        def executemany(self, *args, **kwargs):
            raise RuntimeError("fixture failure after DELETE and before replacement INSERT")

    @contextmanager
    def failing_connect(**kwargs):
        with real_connect(**kwargs) as connection:
            yield FailingInsert(connection)

    monkeypatch.setattr(repository, "connect", failing_connect)
    path.write_text("trade_date,is_trading_day\n2026-10-24,1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="fixture failure"):
        import_calendar(repository, path, source="fixture", source_version="v2", verified=True)
    state = load_calendar(repository)
    assert state.days[date(2026, 10, 24)] is False
    assert state.describe(date(2026, 10, 24))["source_version"] == "v1"
    assert len(repository.query("SELECT * FROM calendar_import")) == 1


@pytest.mark.parametrize("raw", ["24:00", "18:60", "18", "18:10:00", "18:10+08:00", 1810, None])
def test_collection_time_requires_explicit_hour_and_minute(tmp_path, raw):
    settings = Settings(root=tmp_path, app={"pipeline": {"scheduled_time": raw}}, products={}, spreads={})
    with pytest.raises(ValueError, match="HH:MM"):
        _ = settings.scheduled_time


@pytest.mark.parametrize("yaml_content", [
    "runtime:\n  timezone: UTC\n",
    "calendar:\n  exchange: SHFE\n",
    "calendar:\n  path: unused.csv\n",
])
def test_unknown_or_unsupported_calendar_settings_are_not_silently_ignored(tmp_path, yaml_content):
    config = tmp_path / "config"
    config.mkdir()
    (config / "app.yaml").write_text(yaml_content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(tmp_path)
