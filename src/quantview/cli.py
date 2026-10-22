from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import date, timedelta

from quantview.config import load_settings
from quantview.data.manual_import import import_market_file
from quantview.data.repository import Repository
from quantview.operations import build_health_snapshot
from quantview.pipeline import DailyPipeline


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _doctor() -> int:
    settings = load_settings()
    modules = ("pandas", "duckdb", "akshare", "streamlit", "plotly", "jinja2", "yaml")
    checks = {module: importlib.util.find_spec(module) is not None for module in modules}
    checks["python_3_11_plus"] = sys.version_info >= (3, 11)
    checks["config_products"] = set(settings.products) == {"LH", "JD", "C"}
    checks["database_parent_writable"] = settings.database_path.parent.exists()
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantview", description="QuantView2027 research workbench")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check local runtime and configuration")
    status = commands.add_parser("status", help="Show data freshness and trial-run health")
    status.add_argument("--date", type=_date, default=date.today())
    status.add_argument("--trial-days", type=int, default=5)
    commands.add_parser("init-db", help="Create or upgrade the DuckDB schema")
    discover = commands.add_parser("discover-contracts", help="Discover and register LH/JD/C contracts")
    discover.add_argument("--date", type=_date, default=date.today())
    daily = commands.add_parser("run-daily", help="Run the daily research pipeline")
    daily.add_argument("--date", type=_date, default=date.today())
    backfill = commands.add_parser("backfill", help="Run the pipeline for a date interval")
    backfill.add_argument("--start", type=_date, required=True)
    backfill.add_argument("--end", type=_date, required=True)
    manual = commands.add_parser("import-file", help="Import a CSV or Excel fallback data file")
    manual.add_argument("path")
    ui = commands.add_parser("ui", help="Open the local Streamlit workbench")
    ui.add_argument("--port", type=int, default=8501)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    settings = load_settings()
    if args.command == "status":
        if not settings.database_path.exists():
            print(
                json.dumps(
                    {"overall_status": "empty", "database": str(settings.database_path)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        snapshot = build_health_snapshot(
            Repository(settings.database_path),
            settings.products,
            as_of=args.date,
            trial_days=max(args.trial_days, 1),
        )
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
        return 0 if snapshot["overall_status"] in {"healthy", "due"} else 1
    pipeline = DailyPipeline(settings)
    if args.command == "init-db":
        Repository(settings.database_path).initialize()
        print(settings.database_path)
        return 0
    if args.command == "discover-contracts":
        pipeline.initialize()
        instruments = pipeline.discover_instruments(args.date)
        print(json.dumps([item.as_record() for item in instruments], ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "run-daily":
        result = pipeline.run(args.date, trigger_type="manual")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["status"] in {"success", "partial"} else 1
    if args.command == "backfill":
        if args.end < args.start:
            raise SystemExit("--end must be on or after --start")
        current = args.start
        exit_code = 0
        while current <= args.end:
            if current.weekday() < 5:
                result = pipeline.run(current, trigger_type="backfill")
                print(json.dumps(result, ensure_ascii=False, default=str))
                if result["status"] == "failed":
                    exit_code = 1
            current += timedelta(days=1)
        return exit_code
    if args.command == "import-file":
        result = import_market_file(settings, settings.root / args.path)
        pipeline._rebuild_analytics()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["accepted"] else 1
    if args.command == "ui":
        app = settings.root / "src" / "quantview" / "ui" / "app.py"
        return subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(app),
                "--server.address=127.0.0.1",
                f"--server.port={args.port}",
            ],
            cwd=settings.root,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

