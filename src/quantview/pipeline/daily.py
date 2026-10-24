from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from quantview import __version__
from quantview.ai import AIWorkflow
from quantview.ai.evidence import build_evidence_pack
from quantview.ai.provider import AIUnavailable
from quantview.analytics import build_analytics
from quantview.config import Settings
from quantview.data.adapters import AkshareEastmoneyAdapter, AkshareSinaAdapter, DataSourceError
from quantview.data.completeness import assess_completeness
from quantview.data.normalization import normalize_daily
from quantview.data.raw_store import save_raw_snapshot
from quantview.data.repository import Repository
from quantview.data.validation import validate_bars
from quantview.domain.models import Instrument
from quantview.domain.rules import parse_contract_code
from quantview.reports import render_daily_report


@contextmanager
def pipeline_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"Another pipeline may be running; lock exists: {path}") from exc
    try:
        os.write(descriptor, f"pid={os.getpid()} time={datetime.now().isoformat()}".encode())
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def classify_run_status(
    rows_accepted: int,
    products_found: int,
    products_expected: int,
    *,
    complete_products: int | None = None,
) -> str:
    """Classify whether the target day's persisted dataset is usable."""
    if products_found == 0:
        return "failed"
    if (
        products_found < products_expected
        or rows_accepted == 0
        or (complete_products is not None and complete_products < products_expected)
    ):
        return "partial"
    return "success"


class DailyPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.repository = Repository(settings.database_path)
        self.universe_sources: dict[str, str] = {}
        self.adapters = {
            "akshare_sina": AkshareSinaAdapter(),
            "akshare_em": AkshareEastmoneyAdapter(),
        }

    def initialize(self) -> None:
        self.repository.initialize()

    def _candidate_contracts(self, product: str, target: date) -> tuple[list[str], bool]:
        config = self.settings.products[product]
        discovered: list[str] = []
        try:
            discovered = self.adapters["akshare_sina"].discover_contracts(product, config["name"])
        except DataSourceError:
            pass
        live_discovery = bool(discovered)
        existing = self.repository.query(
            "SELECT contract_code FROM instrument WHERE product=? AND is_active=true", [product]
        )
        if live_discovery:
            return sorted(set(discovered)), True
        candidates = set(existing.get("contract_code", pd.Series(dtype=str)).tolist())
        if not candidates:
            allowed = set(config["allowed_delivery_months"])
            for offset in range(0, 13):
                month_index = target.year * 12 + target.month - 1 + offset
                year, month_zero = divmod(month_index, 12)
                month = month_zero + 1
                if month in allowed:
                    candidates.add(f"{product}{year % 100:02d}{month:02d}")
        return sorted(candidates), False

    def discover_instruments(self, target: date) -> list[Instrument]:
        instruments: list[Instrument] = []
        self.universe_sources = {}
        for product, config in self.settings.products.items():
            contracts, live_discovery = self._candidate_contracts(product, target)
            self.universe_sources[product] = "live_discovery" if live_discovery else "local_candidates"
            product_instruments: list[Instrument] = []
            for contract in contracts:
                try:
                    instrument = parse_contract_code(
                        contract,
                        exchange=config["exchange"],
                        reference_date=target,
                        multiplier=config.get("multiplier"),
                        price_tick=config.get("price_tick"),
                    )
                except ValueError:
                    continue
                if instrument.product == product:
                    product_instruments.append(instrument)
            self.repository.upsert_instruments(product_instruments)
            if live_discovery:
                self.repository.set_active_instruments(
                    product, [item.instrument_id for item in product_instruments]
                )
            instruments.extend(product_instruments)
        unique = {item.instrument_id: item for item in instruments}
        return sorted(unique.values(), key=lambda item: item.instrument_id)

    def _fetch_instrument(
        self,
        instrument: Instrument,
        start: date,
        end: date,
        source_errors: list[dict],
    ) -> tuple[int, int, int]:
        priority = self.settings.app["data_sources"]["daily_priority"]
        fetched = rejected_count = 0
        stored_keys: set[tuple] = set()
        for source_name in priority:
            adapter = self.adapters[source_name]
            try:
                result = adapter.fetch_daily(instrument.source_symbol, start, end)
                fetched += len(result.frame)
                if self.settings.app["data_sources"].get("save_raw_snapshots", True):
                    save_raw_snapshot(result, self.settings.path("raw"))
                bars = normalize_daily(result, instrument)
                accepted, rejected = validate_bars(bars)
                rejected_count += len(rejected) + max(0, len(result.frame) - len(bars))
                # Preserve valid primary-source history when the backup fills a gap.
                new_bars = [
                    bar for bar in accepted
                    if (bar.trade_date, bar.instrument_id) not in stored_keys
                ]
                self.repository.upsert_bars(new_bars)
                stored_keys.update((bar.trade_date, bar.instrument_id) for bar in new_bars)
                if any(bar.trade_date == end for bar in accepted):
                    return fetched, len(stored_keys), rejected_count
                raise DataSourceError(f"No valid target-date bar for {end}; trying next source")
            except Exception as exc:
                source_errors.append(
                    {
                        "product": instrument.product,
                        "symbol": instrument.contract_code,
                        "source": source_name,
                        "error": str(exc),
                    }
                )
        return fetched, len(stored_keys), rejected_count

    def _rebuild_analytics(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        daily = self.repository.query("SELECT * FROM futures_daily ORDER BY trade_date, instrument_id")
        instruments = self.repository.query("SELECT * FROM instrument")
        product_daily, spreads = build_analytics(
            daily,
            instruments,
            self.settings.products,
            flat_threshold=float(
                self.settings.app.get("analytics", {}).get("flat_return_threshold", 0.001)
            ),
        )
        dates = sorted(set(product_daily["trade_date"])) if not product_daily.empty else []
        self.repository.replace_frame("product_daily", product_daily, "trade_date", dates)
        spread_dates = sorted(set(spreads["trade_date"])) if not spreads.empty else []
        self.repository.replace_frame("calendar_spread_daily", spreads, "trade_date", spread_dates)
        return product_daily, spreads

    def _run_ai(
        self,
        target: date,
        product_daily: pd.DataFrame,
        spreads: pd.DataFrame,
        source_errors: list[dict],
        completeness: dict[str, dict],
    ) -> list[str]:
        ai_config = self.settings.app.get("ai", {})
        if not ai_config.get("enabled", True):
            return []
        target_rows = product_daily.loc[
            (pd.to_datetime(product_daily["trade_date"]).dt.date == target)
            & product_daily["product"].isin(
                [key for key, value in completeness.items() if value["status"] == "complete"]
            )
        ]
        if target_rows.empty:
            return []
        try:
            workflow = AIWorkflow(
                self.settings.root,
                self.settings.ai_model,
                timeout=float(ai_config.get("timeout_seconds", 90)),
            )
        except AIUnavailable:
            return []
        target_spreads = spreads.loc[pd.to_datetime(spreads["trade_date"]).dt.date == target]
        completed = []
        for _, row in target_rows.iterrows():
            product = row["product"]
            pack = build_evidence_pack(
                target,
                product,
                row,
                target_spreads.loc[target_spreads["product"] == product],
                completeness[product],
            )
            try:
                self.repository.upsert_ai_analysis(workflow.run(pack))
                completed.append(product)
            except Exception as exc:
                source_errors.append({"product": product, "source": "ai", "error": str(exc)})
        return completed

    def run(self, target: date, trigger_type: str = "manual") -> dict:
        self.initialize()
        run_id = uuid.uuid4().hex
        self.repository.start_run(run_id, target, trigger_type, __version__)
        stages: dict[str, dict] = {}
        source_errors: list[dict] = []
        fetched = accepted = rejected = 0
        report_path: str | None = None
        status = "failed"
        lock_path = self.settings.root / self.settings.app["pipeline"]["lock_file"]
        try:
            with pipeline_lock(lock_path):
                instruments = self.discover_instruments(target)
                stages["discover"] = {"status": "success", "instruments": len(instruments)}
                lookback = int(self.settings.app["pipeline"].get("default_lookback_days", 90))
                start = target - timedelta(days=lookback)
                for instrument in instruments:
                    counts = self._fetch_instrument(instrument, start, target, source_errors)
                    fetched += counts[0]
                    accepted += counts[1]
                    rejected += counts[2]
                stages["fetch"] = {
                    "status": "success" if accepted else "failed",
                    "fetched": fetched,
                    "accepted": accepted,
                    "rejected": rejected,
                }
                self.repository.update_daily_changes()
                target_bars = self.repository.query(
                    """
                    SELECT f.*, i.product, i.contract_code
                    FROM futures_daily f JOIN instrument i USING (instrument_id)
                    WHERE f.trade_date=?
                    """,
                    [target],
                )
                completeness = assess_completeness(
                    target, instruments, target_bars, self.settings.products,
                    min_contracts=int(
                        self.settings.app.get("data_quality", {}).get("min_contracts_per_product", 2)
                    ),
                    universe_sources=self.universe_sources,
                )
                complete_count = sum(
                    item["status"] == "complete" for item in completeness.values()
                )
                stages["completeness"] = {
                    "status": (
                        "success" if complete_count == len(self.settings.products)
                        else "failed" if target_bars.empty else "partial"
                    ),
                    "products": completeness,
                }
                product_daily, spreads = self._rebuild_analytics()
                target_products = product_daily.loc[
                    pd.to_datetime(product_daily["trade_date"]).dt.date == target
                ]
                product_count = len(target_products)
                stages["analytics"] = {
                    "status": (
                        "success"
                        if product_count == len(self.settings.products)
                        else "failed" if product_count == 0 else "partial"
                    ),
                    "products": product_count,
                }
                ai_products = self._run_ai(
                    target, product_daily, spreads, source_errors, completeness
                )
                stages["ai"] = {
                    "status": "success" if ai_products else "skipped",
                    "products": len(ai_products),
                    "completed_products": ai_products,
                    "blocked_products": [
                        key for key, value in completeness.items() if value["status"] != "complete"
                    ],
                }
                status = classify_run_status(
                    accepted, product_count, len(self.settings.products),
                    complete_products=complete_count,
                )
                if status == "failed":
                    stages["report"] = {
                        "status": "skipped",
                        "reason": "No target-date product data",
                    }
                else:
                    run_meta = {
                        "status": status,
                        "rows_accepted": accepted,
                        "rows_rejected": rejected,
                        "source_errors": source_errors,
                        "completeness": completeness,
                        "ai_products": ai_products,
                    }
                    output = render_daily_report(
                        self.repository, self.settings.root, target, run_meta
                    )
                    report_path = str(output)
                    stages["report"] = {"status": "success", "path": report_path}
        except Exception as exc:
            status = "failed"
            source_errors.append({"source": "pipeline", "error": str(exc)})
            stages.setdefault("pipeline", {"status": "failed", "error": str(exc)})
        finally:
            self.repository.finish_run(
                run_id,
                status=status,
                stages=stages,
                rows_fetched=fetched,
                rows_accepted=accepted,
                rows_rejected=rejected,
                source_errors=source_errors,
                report_path=report_path,
            )
        return {
            "run_id": run_id,
            "target_trade_date": target.isoformat(),
            "status": status,
            "stages": stages,
            "rows_fetched": fetched,
            "rows_accepted": accepted,
            "rows_rejected": rejected,
            "source_errors": source_errors,
            "report_path": report_path,
        }
