from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=max(5, window // 4)).mean()
    std = series.rolling(window, min_periods=max(5, window // 4)).std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)


def _rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    minimum = max(5, window // 4)
    return series.rolling(window, min_periods=minimum).apply(
        lambda values: pd.Series(values).rank(pct=True).iloc[-1], raw=False
    )


def _regime(price_return: float, oi_change: float, flat_threshold: float) -> str:
    if pd.isna(price_return) or pd.isna(oi_change):
        return "unknown"
    if abs(price_return) < flat_threshold:
        return "flat"
    if price_return > 0:
        return "up_add" if oi_change > 0 else "up_reduce"
    return "down_add" if oi_change > 0 else "down_reduce"


def _prepare_bars(daily: pd.DataFrame, instruments: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return daily.copy()
    columns = [
        "instrument_id",
        "product",
        "contract_code",
        "delivery_year",
        "delivery_month",
    ]
    bars = daily.merge(instruments[columns], on="instrument_id", how="inner")
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    bars["delivery_ordinal"] = bars["delivery_year"] * 12 + bars["delivery_month"]
    bars = bars.sort_values(["instrument_id", "trade_date"]).reset_index(drop=True)
    previous_close = bars.groupby("instrument_id")["close"].shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous_close).abs(),
            (bars["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["contract_atr_20"] = true_range.groupby(bars["instrument_id"]).transform(
        lambda values: values.rolling(20, min_periods=5).mean()
    )
    return bars


def build_product_daily(bars: pd.DataFrame, flat_threshold: float = 0.001) -> pd.DataFrame:
    columns = [
        "trade_date", "product", "main_contract", "second_contract", "main_changed",
        "previous_main_contract", "selection_method", "main_close", "main_settlement",
        "total_volume", "total_open_interest", "total_volume_change", "total_oi_change",
        "main_volume_share", "main_oi_share", "price_oi_regime", "return_5d",
        "return_20d", "atr_20", "volume_zscore_20", "oi_change_zscore_20",
    ]
    if bars.empty:
        return pd.DataFrame(columns=columns)
    eligible = bars.loc[bars["quality_status"] != "rejected"].copy()
    eligible = eligible.sort_values(
        ["trade_date", "product", "open_interest", "volume", "delivery_ordinal"],
        ascending=[True, True, False, False, True],
    )
    eligible["rank"] = eligible.groupby(["trade_date", "product"]).cumcount() + 1
    main = eligible.loc[eligible["rank"] == 1].copy()
    second = eligible.loc[eligible["rank"] == 2, ["trade_date", "product", "contract_code"]].rename(
        columns={"contract_code": "second_contract"}
    )
    totals = eligible.groupby(["trade_date", "product"], as_index=False).agg(
        total_volume=("volume", "sum"), total_open_interest=("open_interest", "sum")
    )
    result = main[
        [
            "trade_date", "product", "contract_code", "close", "settlement", "volume",
            "open_interest", "settlement_return", "contract_atr_20",
        ]
    ].rename(
        columns={
            "contract_code": "main_contract",
            "close": "main_close",
            "settlement": "main_settlement",
            "contract_atr_20": "atr_20",
            "settlement_return": "main_daily_return",
        }
    )
    result = result.merge(second, on=["trade_date", "product"], how="left").merge(
        totals, on=["trade_date", "product"], how="left"
    )
    result = result.sort_values(["product", "trade_date"]).reset_index(drop=True)
    result["previous_main_contract"] = result.groupby("product")["main_contract"].shift(1)
    result["main_changed"] = (
        result["previous_main_contract"].notna()
        & (result["main_contract"] != result["previous_main_contract"])
    )
    result["selection_method"] = "open_interest"
    result["total_volume_change"] = result.groupby("product")["total_volume"].diff()
    result["total_oi_change"] = result.groupby("product")["total_open_interest"].diff()
    result["main_volume_share"] = result["volume"] / result["total_volume"].replace(0, np.nan)
    result["main_oi_share"] = result["open_interest"] / result["total_open_interest"].replace(0, np.nan)
    result["price_oi_regime"] = [
        _regime(price, oi, flat_threshold)
        for price, oi in zip(
            result["main_daily_return"], result["total_oi_change"], strict=False
        )
    ]
    for window in (5, 20):
        result[f"return_{window}d"] = result.groupby("product")["main_daily_return"].transform(
            lambda values, w=window: (1 + values).rolling(w, min_periods=w).apply(np.prod, raw=True) - 1
        )
    result["volume_zscore_20"] = result.groupby("product")["total_volume"].transform(
        lambda values: _rolling_zscore(values, 20)
    )
    result["oi_change_zscore_20"] = result.groupby("product")["total_oi_change"].transform(
        lambda values: _rolling_zscore(values, 20)
    )
    result["trade_date"] = result["trade_date"].dt.date
    return result[columns]


def _spread_rows_for_day(day: pd.DataFrame, main_row: pd.Series | None) -> list[dict]:
    rows: list[dict] = []
    ordered = day.sort_values("delivery_ordinal")
    for index in range(len(ordered) - 1):
        rows.append(_make_spread_row(ordered.iloc[index], ordered.iloc[index + 1], "adjacent"))
    if main_row is not None and pd.notna(main_row.get("second_contract")):
        near_far = day.loc[day["contract_code"].isin([main_row["main_contract"], main_row["second_contract"]])]
        if len(near_far) == 2:
            near_far = near_far.sort_values("delivery_ordinal")
            rows.append(_make_spread_row(near_far.iloc[0], near_far.iloc[1], "main_second"))
    return rows


def _make_spread_row(near: pd.Series, far: pd.Series, pair_type: str) -> dict:
    settlement_spread = near["settlement"] - far["settlement"]
    return {
        "trade_date": near["trade_date"],
        "product": near["product"],
        "near_instrument_id": near["instrument_id"],
        "far_instrument_id": far["instrument_id"],
        "near_contract": near["contract_code"],
        "far_contract": far["contract_code"],
        "near_delivery_ordinal": int(near["delivery_ordinal"]),
        "far_delivery_ordinal": int(far["delivery_ordinal"]),
        "close_spread": near["close"] - far["close"],
        "settlement_spread": settlement_spread,
        "spread_pct": settlement_spread / near["settlement"] if near["settlement"] else math.nan,
        "near_volume": int(near["volume"]),
        "far_volume": int(far["volume"]),
        "near_oi": int(near["open_interest"]),
        "far_oi": int(far["open_interest"]),
        "pair_type": pair_type,
    }


def build_spreads(bars: pd.DataFrame, product_daily: pd.DataFrame, products: dict) -> pd.DataFrame:
    columns = [
        "trade_date", "product", "near_instrument_id", "far_instrument_id", "near_contract",
        "far_contract", "near_delivery_ordinal", "far_delivery_ordinal", "close_spread",
        "settlement_spread", "spread_pct", "spread_change", "zscore_20", "zscore_60",
        "percentile_60", "near_volume", "far_volume", "near_oi", "far_oi",
        "liquidity_status", "pair_type",
    ]
    if bars.empty:
        return pd.DataFrame(columns=columns)
    main_map = {
        (pd.Timestamp(row.trade_date), row.product): row
        for row in product_daily.itertuples(index=False)
    }
    rows: list[dict] = []
    eligible = bars.loc[bars["quality_status"] != "rejected"].copy()
    for (trade_date, product), day in eligible.groupby(["trade_date", "product"]):
        main = main_map.get((pd.Timestamp(trade_date), product))
        main_series = pd.Series(main._asdict()) if main is not None else None
        rows.extend(_spread_rows_for_day(day, main_series))
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=columns)
    thresholds = {key: value for key, value in products.items()}
    frame["liquidity_status"] = [
        "ok"
        if min(row.near_volume, row.far_volume) >= thresholds[row.product].get("min_volume", 0)
        and min(row.near_oi, row.far_oi) >= thresholds[row.product].get("min_open_interest", 0)
        else "thin"
        for row in frame.itertuples()
    ]
    keys = ["near_instrument_id", "far_instrument_id", "pair_type"]
    frame = frame.sort_values([*keys, "trade_date"])
    frame["spread_change"] = frame.groupby(keys)["settlement_spread"].diff()
    for window in (20, 60):
        frame[f"zscore_{window}"] = frame.groupby(keys)["settlement_spread"].transform(
            lambda values, w=window: _rolling_zscore(values, w)
        )
    frame["percentile_60"] = frame.groupby(keys)["settlement_spread"].transform(
        lambda values: _rolling_percentile(values, 60)
    )
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    return frame[columns]


def build_analytics(
    daily: pd.DataFrame,
    instruments: pd.DataFrame,
    products: dict,
    flat_threshold: float = 0.001,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    bars = _prepare_bars(daily, instruments)
    product_daily = build_product_daily(bars, flat_threshold=flat_threshold)
    spreads = build_spreads(bars, product_daily, products)
    return product_daily, spreads
