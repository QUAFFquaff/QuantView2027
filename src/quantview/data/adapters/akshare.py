from __future__ import annotations

import re
import uuid
from datetime import date, datetime

import pandas as pd

from quantview.data.adapters.base import DailyDataAdapter, DataSourceError
from quantview.domain.models import FetchResult


def _akshare():
    try:
        import akshare as ak
    except ImportError as exc:
        raise DataSourceError("AKShare is not installed") from exc
    return ak


class AkshareSinaAdapter(DailyDataAdapter):
    name = "akshare_sina"

    def fetch_daily(self, symbol: str, start: date, end: date) -> FetchResult:
        ak = _akshare()
        fetched_at = datetime.now().astimezone()
        try:
            frame = ak.futures_zh_daily_sina(symbol=symbol)
        except Exception as exc:
            raise DataSourceError(f"Sina daily fetch failed for {symbol}: {exc}") from exc
        if frame is None or frame.empty:
            raise DataSourceError(f"Sina returned no daily data for {symbol}")
        frame = frame.copy()
        dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame = frame.loc[(dates >= start) & (dates <= end)].copy()
        if frame.empty:
            raise DataSourceError(f"Sina has no rows for {symbol} in {start}..{end}")
        return FetchResult(
            source=self.name,
            source_symbol=symbol,
            fetched_at=fetched_at,
            raw_batch_id=uuid.uuid4().hex,
            frame=frame,
            metadata={"endpoint": "futures_zh_daily_sina", "start": str(start), "end": str(end)},
        )

    def discover_contracts(self, product: str, product_name: str) -> list[str]:
        ak = _akshare()
        try:
            frame = ak.futures_zh_realtime(symbol=product_name)
        except Exception as exc:
            raise DataSourceError(f"Sina contract discovery failed for {product}: {exc}") from exc
        if frame is None or frame.empty or "symbol" not in frame:
            return []
        pattern = re.compile(rf"^{re.escape(product)}\d{{3,4}}$", re.IGNORECASE)
        return sorted({str(value).upper() for value in frame["symbol"] if pattern.fullmatch(str(value))})


class AkshareEastmoneyAdapter(DailyDataAdapter):
    name = "akshare_em"

    def fetch_daily(self, symbol: str, start: date, end: date) -> FetchResult:
        ak = _akshare()
        fetched_at = datetime.now().astimezone()
        try:
            frame = ak.futures_hist_em(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as exc:
            raise DataSourceError(f"Eastmoney daily fetch failed for {symbol}: {exc}") from exc
        if frame is None or frame.empty:
            raise DataSourceError(f"Eastmoney returned no daily data for {symbol}")
        return FetchResult(
            source=self.name,
            source_symbol=symbol,
            fetched_at=fetched_at,
            raw_batch_id=uuid.uuid4().hex,
            frame=frame.copy(),
            metadata={"endpoint": "futures_hist_em", "start": str(start), "end": str(end)},
        )
