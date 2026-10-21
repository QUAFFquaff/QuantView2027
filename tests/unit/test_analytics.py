from datetime import date, timedelta

import pandas as pd
import pytest

from quantview.analytics.engine import build_analytics


def sample_frames():
    instruments = pd.DataFrame(
        [
            {"instrument_id": "DCE.C.2701", "product": "C", "contract_code": "C2701", "delivery_year": 2027, "delivery_month": 1},
            {"instrument_id": "DCE.C.2703", "product": "C", "contract_code": "C2703", "delivery_year": 2027, "delivery_month": 3},
        ]
    )
    rows = []
    start = date(2026, 9, 1)
    for offset in range(25):
        trade_date = start + timedelta(days=offset)
        first_is_main = offset < 15
        for contract, base, oi in (
            ("DCE.C.2701", 2400, 5000 if first_is_main else 3000),
            ("DCE.C.2703", 2420, 3000 if first_is_main else 6000),
        ):
            close = base + offset
            rows.append(
                {
                    "trade_date": trade_date,
                    "instrument_id": contract,
                    "open": close - 2,
                    "high": close + 5,
                    "low": close - 5,
                    "close": close,
                    "settlement": close,
                    "volume": 1000 + offset,
                    "open_interest": oi,
                    "settlement_return": None if offset == 0 else 1 / (base + offset - 1),
                    "quality_status": "ok",
                }
            )
    return pd.DataFrame(rows), instruments


def test_main_switch_uses_contract_returns_not_level_jump():
    daily, instruments = sample_frames()
    product_daily, spreads = build_analytics(
        daily,
        instruments,
        {"C": {"min_volume": 1, "min_open_interest": 1}},
    )
    switched = product_daily.loc[product_daily["main_changed"]]
    assert len(switched) == 1
    assert switched.iloc[0]["main_contract"] == "C2703"
    assert product_daily["return_5d"].dropna().abs().max() < 0.01
    assert not spreads.empty


def test_calendar_spread_is_near_minus_far():
    daily, instruments = sample_frames()
    _, spreads = build_analytics(daily, instruments, {"C": {"min_volume": 1, "min_open_interest": 1}})
    adjacent = spreads.loc[spreads["pair_type"] == "adjacent"]
    assert adjacent.iloc[0]["settlement_spread"] == pytest.approx(-20.0)

