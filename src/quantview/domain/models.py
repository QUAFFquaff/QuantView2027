from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    exchange: str
    product: str
    contract_code: str
    delivery_year: int
    delivery_month: int
    source_symbol: str
    contract_multiplier: float | None = None
    price_tick: float | None = None
    listed_date: date | None = None
    last_trade_date: date | None = None
    is_active: bool = True

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DailyBar:
    trade_date: date
    instrument_id: str
    open: float
    high: float
    low: float
    close: float
    settlement: float
    volume: int
    open_interest: int
    source: str
    source_fetched_at: datetime
    raw_batch_id: str
    pre_settlement: float | None = None
    turnover: float | None = None
    quality_status: str = "ok"
    quality_flags: tuple[str, ...] = field(default_factory=tuple)

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["quality_flags"] = list(self.quality_flags)
        return record


@dataclass(frozen=True)
class FetchResult:
    source: str
    source_symbol: str
    fetched_at: datetime
    raw_batch_id: str
    frame: Any
    metadata: dict[str, Any] = field(default_factory=dict)

