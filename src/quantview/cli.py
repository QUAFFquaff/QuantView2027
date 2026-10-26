from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import date

from quantview.config import load_settings
from quantview.data.manual_import import import_market_file
from quantview.data.repository import Repository
from quantview.operations import build_health_snapshot
from quantview.operations.calendar import (
    CalendarUnavailable,
    backfill_targets,
    import_calendar,
    load_calendar,
)
from quantview.operations.clock import shanghai_now
from quantview.pipeline import DailyPipeline


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _print(value: dict | list, *, indent: bool = True) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2 if indent else None, default=str))


def _doctor() -> int:
    settings = load_settings()
    modules = ("pandas", "duckdb", "akshare", "streamlit", "plotly", "jinja2", "yaml", "tzdata")
    checks = {module: importlib.util.find_spec(module) is not None for module in modules}
    checks["python_3_11_plus"] = sys.version_info >= (3, 11)
    checks["config_products"] = set(settings.products) == {"LH", "JD", "C"}
    checks["database_parent_writable"] = settings.database_path.parent.exists()
    _print(checks)
    return 0 if all(checks.values()) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantview", description="QuantView2027 research workbench")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check local runtime and configuration")
    status = commands.add_parser("status", help="Show verified-calendar freshness and missing dates")
    status.add_argument("--date", type=_date, help="Evaluate at the Shanghai collection cutoff on this date")
    status.add_argument("--trial-days", type=int, default=5)
    commands.add_parser("init-db", help="Create the DuckDB schema, including calendar tables")
    calendar = commands.add_parser("import-calendar", help="Import a verified natural-day calendar CSV")
    calendar.add_argument("path")
    calendar.add_argument("--source", required=True, help="Verified source name or URL")
    calendar.add_argument("--version", required=True, help="Source release or verification version")
    calendar.add_argument("--verified", action="store_true", required=True, help="Confirm you verified the file")
    discover = commands.add_parser("discover-contracts", help="Discover and register LH/JD/C contracts")
    discover.add_argument("--date", type=_date)
    daily = commands.add_parser("run-daily", help="Run for the most recent verified day due after 18:10 Shanghai")
    daily.add_argument("--date", type=_date, help="Explicit manual date; unknown calendar yields a warning and partial status")
    daily.add_argument("--trigger", choices=("manual", "scheduled", "rebuild"), default="manual")
    daily.add_argument("--no-ai", action="store_true")
    backfill = commands.add_parser("backfill", help="Run only verified trading days; AI disabled by default")
    backfill.add_argument("--start", type=_date, required=True)
    backfill.add_argument("--end", type=_date, required=True)
    backfill.add_argument("--with-ai", action="store_true", help="Explicitly enable historical AI analysis")
    catch_up = commands.add_parser("catch-up", help="Explicitly rerun the latest missing verified trading day")
    catch_up.add_argument("--all", action="store_true", help="Explicitly rerun the whole displayed missing interval")
    manual = commands.add_parser("import-file", help="Import a CSV or Excel fallback data file")
    manual.add_argument("path")
    ui = commands.add_parser("ui", help="Open the local Streamlit workbench")
    ui.add_argument("--port", type=int, default=8501)
    return parser


def _execute(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        return _doctor()
    settings = load_settings()
    repository = Repository(settings.database_path)
    calendar = load_calendar(repository, settings.calendar_exchange)
    if args.command == "status":
        health = build_health_snapshot(
            repository, settings.products, as_of=args.date, calendar=calendar,
            ready_time=settings.scheduled_time, trial_days=max(args.trial_days, 1),
        )
        _print(health)
        return 0 if health["overall_status"] in {"healthy", "due"} else 1
    if args.command == "import-calendar":
        _print(import_calendar(
            repository, settings.root / args.path, source=args.source,
            source_version=args.version, verified=args.verified, exchange=settings.calendar_exchange,
        ))
        return 0
    if args.command == "init-db":
        repository.initialize()
        print(settings.database_path)
        return 0
    pipeline = DailyPipeline(settings)
    if args.command == "discover-contracts":
        pipeline.initialize()
        instruments = pipeline.discover_instruments(args.date or shanghai_now().date())
        _print([item.as_record() for item in instruments])
        return 0
    if args.command == "run-daily":
        result = pipeline.run(args.date, trigger_type=args.trigger, allow_ai=False if args.no_ai else None)
        _print(result)
        return 0 if result["status"] in {"success", "partial", "skipped"} else 1
    if args.command == "backfill":
        instant = shanghai_now()
        targets = backfill_targets(calendar, args.start, args.end, now=instant, ready_time=settings.scheduled_time)
        exit_code = 0
        for target in targets:
            result = pipeline.run(target, trigger_type="backfill", now=instant, allow_ai=args.with_ai)
            _print(result, indent=False)
            if result["status"] == "failed":
                exit_code = 1
        if not targets:
            _print({"status": "skipped", "reason": "所选区间没有已核验交易日"})
        return exit_code
    if args.command == "catch-up":
        health = build_health_snapshot(repository, settings.products, calendar=calendar, ready_time=settings.scheduled_time)
        plan = health["catch_up"]
        if not plan["available"]:
            raise CalendarUnavailable(plan["reason"])
        targets = plan["missing_dates"] if args.all else plan["missing_dates"][-1:]
        exit_code = 0
        for value in targets:
            result = pipeline.run(date.fromisoformat(value), trigger_type="catch_up")
            _print(result, indent=False)
            if result["status"] == "failed":
                exit_code = 1
        if not targets:
            _print({"status": "skipped", "reason": "没有缺失的已核验交易日"})
        return exit_code
    if args.command == "import-file":
        result = import_market_file(settings, settings.root / args.path)
        pipeline._rebuild_analytics()
        _print(result)
        return 0 if result["accepted"] else 1
    if args.command == "ui":
        app = settings.root / "src" / "quantview" / "ui" / "app.py"
        return subprocess.call(
            [sys.executable, "-m", "streamlit", "run", str(app),
             "--server.address=127.0.0.1", f"--server.port={args.port}"],
            cwd=settings.root,
        )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _execute(args)
    except (ValueError, OSError) as exc:
        _print({"status": "failed", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
