from __future__ import annotations

from datetime import date

import pandas as pd

from quantview.domain.models import DailyBar, FetchResult, Instrument

FIELD_ALIASES = {
    "date": ("date", "时间", "日期"),
    "open": ("open", "开盘"),
    "high": ("high", "最高"),
    "low": ("low", "最低"),
    "close": ("close", "收盘"),
    "settlement": ("settle", "settlement", "结算价"),
    "pre_settlement": ("pre_settle", "presettlement", "前结算价"),
    "volume": ("volume", "成交量"),
    "open_interest": ("hold", "open_interest", "position", "持仓量"),
    "turnover": ("turnover", "amount", "成交额"),
}


def _column(frame: pd.DataFrame, canonical: str) -> pd.Series | None:
    for name in FIELD_ALIASES[canonical]:
        if name in frame.columns:
            return frame[name]
    return None


def normalize_daily(result: FetchResult, instrument: Instrument) -> list[DailyBar]:
    frame = result.frame.copy()
    required = ("date", "open", "high", "low", "close", "volume", "open_interest")
    missing = [name for name in required if _column(frame, name) is None]
    if missing:
        raise ValueError(f"{result.source} is missing required fields: {', '.join(missing)}")
    normalized = pd.DataFrame()
    normalized["trade_date"] = pd.to_datetime(_column(frame, "date"), errors="coerce").dt.date
    for name in ("open", "high", "low", "close", "volume", "open_interest"):
        normalized[name] = pd.to_numeric(_column(frame, name), errors="coerce")
    for name in ("settlement", "pre_settlement", "turnover"):
        source = _column(frame, name)
        normalized[name] = pd.to_numeric(source, errors="coerce") if source is not None else None

    bars: list[DailyBar] = []
    for row in normalized.itertuples(index=False):
        if not isinstance(row.trade_date, date):
            continue
        flags: list[str] = []
        settlement = row.settlement
        if pd.isna(settlement):
            settlement = row.close
            flags.append("settlement_filled_from_close")
        required_values = [row.open, row.high, row.low, row.close, settlement, row.volume, row.open_interest]
        if any(pd.isna(value) for value in required_values):
            continue
        bars.append(
            DailyBar(
                trade_date=row.trade_date,
                instrument_id=instrument.instrument_id,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                settlement=float(settlement),
                volume=int(row.volume),
                open_interest=int(row.open_interest),
                source=result.source,
                source_fetched_at=result.fetched_at,
                raw_batch_id=result.raw_batch_id,
                pre_settlement=None if pd.isna(row.pre_settlement) else float(row.pre_settlement),
                turnover=None if pd.isna(row.turnover) else float(row.turnover),
                quality_status="warning" if flags else "ok",
                quality_flags=tuple(flags),
            )
        )
    return bars
