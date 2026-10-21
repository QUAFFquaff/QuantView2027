from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from quantview.domain.models import DailyBar, Instrument

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS instrument (
    instrument_id VARCHAR PRIMARY KEY,
    exchange VARCHAR NOT NULL,
    product VARCHAR NOT NULL,
    contract_code VARCHAR NOT NULL,
    delivery_year INTEGER NOT NULL,
    delivery_month INTEGER NOT NULL,
    listed_date DATE,
    last_trade_date DATE,
    contract_multiplier DOUBLE,
    price_tick DOUBLE,
    is_active BOOLEAN NOT NULL,
    source_symbol VARCHAR NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS futures_daily (
    trade_date DATE NOT NULL,
    instrument_id VARCHAR NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    settlement DOUBLE NOT NULL,
    pre_settlement DOUBLE,
    volume BIGINT NOT NULL,
    open_interest BIGINT NOT NULL,
    turnover DOUBLE,
    close_change DOUBLE,
    close_return DOUBLE,
    settlement_return DOUBLE,
    volume_change BIGINT,
    volume_change_pct DOUBLE,
    oi_change BIGINT,
    oi_change_pct DOUBLE,
    source VARCHAR NOT NULL,
    source_fetched_at TIMESTAMP NOT NULL,
    raw_batch_id VARCHAR NOT NULL,
    quality_status VARCHAR NOT NULL,
    quality_flags JSON NOT NULL,
    PRIMARY KEY (trade_date, instrument_id)
);

CREATE TABLE IF NOT EXISTS product_daily (
    trade_date DATE NOT NULL,
    product VARCHAR NOT NULL,
    main_contract VARCHAR,
    second_contract VARCHAR,
    main_changed BOOLEAN,
    previous_main_contract VARCHAR,
    selection_method VARCHAR,
    main_close DOUBLE,
    main_settlement DOUBLE,
    total_volume BIGINT,
    total_open_interest BIGINT,
    total_volume_change BIGINT,
    total_oi_change BIGINT,
    main_volume_share DOUBLE,
    main_oi_share DOUBLE,
    price_oi_regime VARCHAR,
    return_5d DOUBLE,
    return_20d DOUBLE,
    atr_20 DOUBLE,
    volume_zscore_20 DOUBLE,
    oi_change_zscore_20 DOUBLE,
    PRIMARY KEY (trade_date, product)
);

CREATE TABLE IF NOT EXISTS calendar_spread_daily (
    trade_date DATE NOT NULL,
    product VARCHAR NOT NULL,
    near_instrument_id VARCHAR NOT NULL,
    far_instrument_id VARCHAR NOT NULL,
    near_contract VARCHAR NOT NULL,
    far_contract VARCHAR NOT NULL,
    near_delivery_ordinal INTEGER NOT NULL,
    far_delivery_ordinal INTEGER NOT NULL,
    close_spread DOUBLE,
    settlement_spread DOUBLE,
    spread_pct DOUBLE,
    spread_change DOUBLE,
    zscore_20 DOUBLE,
    zscore_60 DOUBLE,
    percentile_60 DOUBLE,
    near_volume BIGINT,
    far_volume BIGINT,
    near_oi BIGINT,
    far_oi BIGINT,
    liquidity_status VARCHAR,
    pair_type VARCHAR NOT NULL,
    PRIMARY KEY (trade_date, near_instrument_id, far_instrument_id, pair_type)
);

CREATE TABLE IF NOT EXISTS ai_analysis (
    analysis_id VARCHAR PRIMARY KEY,
    trade_date DATE NOT NULL,
    product VARCHAR NOT NULL,
    bull_case TEXT,
    bear_case TEXT,
    judge_view TEXT,
    bias VARCHAR,
    confidence DOUBLE,
    key_evidence JSON,
    risk_flags JSON,
    invalidation_conditions JSON,
    provider VARCHAR,
    model VARCHAR,
    prompt_version VARCHAR,
    input_hash VARCHAR,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id VARCHAR PRIMARY KEY,
    target_trade_date DATE NOT NULL,
    trigger_type VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status VARCHAR NOT NULL,
    stages JSON NOT NULL,
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    rows_accepted INTEGER NOT NULL DEFAULT 0,
    rows_rejected INTEGER NOT NULL DEFAULT 0,
    source_errors JSON NOT NULL,
    report_path VARCHAR,
    code_version VARCHAR
);
"""


class Repository:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self, *, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
        connection = duckdb.connect(str(self.path), read_only=read_only)
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(SCHEMA_SQL)

    def query(self, sql: str, parameters: list | tuple | None = None) -> pd.DataFrame:
        with self.connect(read_only=True) as connection:
            return connection.execute(sql, parameters or []).fetchdf()

    def upsert_instruments(self, instruments: list[Instrument]) -> int:
        if not instruments:
            return 0
        now = datetime.now().astimezone()
        frame = pd.DataFrame([{**item.as_record(), "updated_at": now} for item in instruments])
        with self.connect() as connection:
            connection.register("incoming_instrument", frame)
            connection.execute(
                "DELETE FROM instrument USING incoming_instrument "
                "WHERE instrument.instrument_id = incoming_instrument.instrument_id"
            )
            connection.execute("INSERT INTO instrument BY NAME SELECT * FROM incoming_instrument")
        return len(frame)

    def set_active_instruments(self, product: str, instrument_ids: list[str]) -> None:
        """Mark only the contracts confirmed by a successful live discovery as active."""
        with self.connect() as connection:
            connection.execute("UPDATE instrument SET is_active=false WHERE product=?", [product])
            if instrument_ids:
                placeholders = ",".join("?" for _ in instrument_ids)
                connection.execute(
                    f"UPDATE instrument SET is_active=true WHERE instrument_id IN ({placeholders})",
                    instrument_ids,
                )

    def upsert_bars(self, bars: list[DailyBar]) -> int:
        if not bars:
            return 0
        rows = []
        for bar in bars:
            row = bar.as_record()
            row["quality_flags"] = json.dumps(row["quality_flags"], ensure_ascii=False)
            row.update(
                {
                    "close_change": None,
                    "close_return": None,
                    "settlement_return": None,
                    "volume_change": None,
                    "volume_change_pct": None,
                    "oi_change": None,
                    "oi_change_pct": None,
                }
            )
            rows.append(row)
        frame = pd.DataFrame(rows)
        with self.connect() as connection:
            connection.register("incoming_daily", frame)
            connection.execute(
                "DELETE FROM futures_daily USING incoming_daily "
                "WHERE futures_daily.trade_date = incoming_daily.trade_date "
                "AND futures_daily.instrument_id = incoming_daily.instrument_id"
            )
            connection.execute("INSERT INTO futures_daily BY NAME SELECT * FROM incoming_daily")
        return len(frame)

    def replace_frame(self, table: str, frame: pd.DataFrame, date_column: str, dates: list[date]) -> int:
        allowed = {"product_daily", "calendar_spread_daily"}
        if table not in allowed:
            raise ValueError(f"Unsupported replace table: {table}")
        if not dates:
            return 0
        placeholders = ",".join("?" for _ in dates)
        with self.connect() as connection:
            connection.execute(f"DELETE FROM {table} WHERE {date_column} IN ({placeholders})", dates)
            if not frame.empty:
                connection.register("incoming_frame", frame)
                connection.execute(f"INSERT INTO {table} BY NAME SELECT * FROM incoming_frame")
        return len(frame)

    def update_daily_changes(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE futures_daily AS target SET
                    close_change = calc.close_change,
                    close_return = calc.close_return,
                    settlement_return = calc.settlement_return,
                    volume_change = calc.volume_change,
                    volume_change_pct = calc.volume_change_pct,
                    oi_change = calc.oi_change,
                    oi_change_pct = calc.oi_change_pct
                FROM (
                    SELECT trade_date, instrument_id,
                        close - lag(close) OVER w AS close_change,
                        close / nullif(lag(close) OVER w, 0) - 1 AS close_return,
                        settlement / nullif(lag(settlement) OVER w, 0) - 1 AS settlement_return,
                        volume - lag(volume) OVER w AS volume_change,
                        volume / nullif(lag(volume) OVER w, 0) - 1 AS volume_change_pct,
                        open_interest - lag(open_interest) OVER w AS oi_change,
                        open_interest / nullif(lag(open_interest) OVER w, 0) - 1 AS oi_change_pct
                    FROM futures_daily
                    WINDOW w AS (PARTITION BY instrument_id ORDER BY trade_date)
                ) calc
                WHERE target.trade_date = calc.trade_date
                  AND target.instrument_id = calc.instrument_id
                """
            )

    def upsert_ai_analysis(self, row: dict) -> None:
        serializable = row.copy()
        for key in ("key_evidence", "risk_flags", "invalidation_conditions"):
            serializable[key] = json.dumps(serializable.get(key, []), ensure_ascii=False)
        frame = pd.DataFrame([serializable])
        with self.connect() as connection:
            connection.register("incoming_ai", frame)
            connection.execute(
                "DELETE FROM ai_analysis USING incoming_ai "
                "WHERE ai_analysis.analysis_id = incoming_ai.analysis_id"
            )
            connection.execute("INSERT INTO ai_analysis BY NAME SELECT * FROM incoming_ai")

    def start_run(self, run_id: str, target: date, trigger_type: str, code_version: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO pipeline_run VALUES (?, ?, ?, ?, NULL, 'running', '{}', 0, 0, 0, '{}', NULL, ?)",
                [run_id, target, trigger_type, datetime.now().astimezone(), code_version],
            )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        stages: dict,
        rows_fetched: int,
        rows_accepted: int,
        rows_rejected: int,
        source_errors: list[dict],
        report_path: str | None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_run SET finished_at=?, status=?, stages=?, rows_fetched=?,
                    rows_accepted=?, rows_rejected=?, source_errors=?, report_path=?
                WHERE run_id=?
                """,
                [
                    datetime.now().astimezone(),
                    status,
                    json.dumps(stages, ensure_ascii=False),
                    rows_fetched,
                    rows_accepted,
                    rows_rejected,
                    json.dumps(source_errors, ensure_ascii=False),
                    report_path,
                    run_id,
                ],
            )
