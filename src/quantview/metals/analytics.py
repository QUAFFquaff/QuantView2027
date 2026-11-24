"""Price-only analytics over verified local snapshots; no network or valuation writes."""
from __future__ import annotations

import math

import pandas as pd


def _number(value, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and (value >= 0 if zero else value > 0) else None


def quote_frame(snapshot: dict) -> pd.DataFrame:
    """Recover optional provider fields from the *same version*, including old caches.

    Only dates and closing prices already admitted by normalization are used.
    A complete, internally consistent OHLC tuple is required for a candle.
    Conflicting duplicate raw rows are never arbitrarily selected.
    """
    raw = {}
    for row in snapshot.get("raw_payload", {}).get("rows", []):
        key = str(row.get("date", ""))
        raw.setdefault(key, []).append(row)
    rows = []
    for quote in snapshot["quotes"]:
        row = {"trade_date": quote["trade_date"], "close": float(quote["close"])}
        candidates = raw.get(quote["trade_date"], [])
        if len(candidates) == 1 and _number(candidates[0].get("close")) == row["close"]:
            source = candidates[0]
            o, h, low = (_number(source.get(key)) for key in ("open", "high", "low"))
            if all(value is not None for value in (o, h, low)) and low <= min(o, row["close"]) <= max(o, row["close"]) <= h:
                row.update(open=o, high=h, low=low)
            volume = _number(source.get("volume"), zero=True)
            if volume is not None:
                row["volume"] = volume
        rows.append(row)
    frame = pd.DataFrame(rows).reindex(columns=["trade_date", "open", "high", "low", "close", "volume"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    for window in (5, 20, 60):
        frame[f"ma{window}"] = frame["close"].rolling(window, min_periods=window).mean()
    return frame


def price_metrics(frame: pd.DataFrame) -> dict:
    prices = frame["close"]
    if prices.empty:
        return {}
    changes = {f"change_{n}": float(prices.iloc[-1] / prices.iloc[-n - 1] - 1) if len(prices) > n else None for n in (1, 5, 20, 60)}
    tail = prices.tail(60)
    changes.update(
        count=len(prices), latest=float(prices.iloc[-1]),
        high_60=float(tail.max()) if len(tail) == 60 else None,
        low_60=float(tail.min()) if len(tail) == 60 else None,
        drawdown_60=float((tail / tail.cummax() - 1).min()) if len(tail) == 60 else None,
        volatility_20=float(prices.pct_change(fill_method=None).tail(20).std(ddof=1)) if len(prices) >= 21 else None,
        ma20_gap=float(prices.iloc[-1] / prices.tail(20).mean() - 1) if len(prices) >= 20 else None,
    )
    return changes


def compare_prices(frames: dict[str, pd.DataFrame], count: int | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Intersect actual source dates before rebasing. Never carry prices forward.

    Correlation uses returns over adjacent *shared observations*, whose intervals
    can span several source sessions, rather than calling them daily returns.
    """
    if len(frames) < 2:
        return pd.DataFrame(), pd.DataFrame()
    joined = pd.concat({name: frame.set_index("trade_date")["close"] for name, frame in frames.items()}, axis=1, join="inner").dropna().sort_index()
    if count is not None:
        joined = joined.tail(count)
    if len(joined) < 2:
        return joined.iloc[:0], pd.DataFrame()
    indexed = joined.div(joined.iloc[0]).mul(100)
    returns = joined.pct_change(fill_method=None).iloc[1:]
    correlation = returns.corr(min_periods=20) if len(returns) >= 20 else pd.DataFrame()
    return indexed, correlation
