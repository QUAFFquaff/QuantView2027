from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from quantview.data.completeness import assess_completeness
from quantview.domain.rules import parse_contract_code

TARGET = date(2026, 10, 23)


def bars(*codes, target=TARGET, status="ok"):
    return pd.DataFrame([
        {
            "trade_date": target,
            "instrument_id": parse_contract_code(code).instrument_id,
            "contract_code": code,
            "product": "C",
            "quality_status": status,
            "quality_flags": "[]",
            "source": "fixture",
        }
        for code in codes
    ])


def assess(instruments, frame, **kwargs):
    return assess_completeness(TARGET, instruments, frame, {"C": {}}, **kwargs)["C"]


def test_one_valid_bar_cannot_claim_product_is_complete():
    instrument = parse_contract_code("C2701")
    quality = assess([instrument], bars("C2701"))
    assert quality["status"] == "partial"
    assert quality["missing_contracts"] == []
    assert quality["minimum_contracts"] == 2
    assert assess([instrument], bars("C2701"), min_contracts=1)["status"] == "complete"


@pytest.mark.parametrize("frame", [
    pd.DataFrame(),
    bars("C2701", "C2703", target=date(2026, 10, 22)),
    bars("C2701", "C2703", status="rejected"),
])
def test_previous_or_rejected_rows_do_not_count(frame):
    quality = assess([parse_contract_code(code) for code in ("C2701", "C2703")], frame)
    assert quality["status"] == "missing"
    assert quality["missing_contracts"] == ["C2701", "C2703"]
    assert quality["available_contracts"] == []


def test_unknown_contract_universe_does_not_claim_complete():
    assert assess([], bars("C2701", "C2703"))["status"] == "partial"


def test_explicit_listing_dates_limit_expected_universe():
    instruments = [parse_contract_code(code) for code in ("C2701", "C2703")]
    instruments += [
        replace(parse_contract_code("C2609"), last_trade_date=date(2026, 9, 15)),
        replace(parse_contract_code("C2705"), listed_date=date(2026, 11, 1)),
    ]
    quality = assess(instruments, bars("C2701", "C2703"))
    assert quality["status"] == "complete"
    assert quality["expected_contracts"] == ["C2701", "C2703"]


def test_rows_outside_known_universe_do_not_satisfy_minimum():
    quality = assess([parse_contract_code("C2701")], bars("C2701", "C2609"))
    assert quality["status"] == "partial"


def test_invalid_minimum_is_rejected():
    with pytest.raises(ValueError, match="positive"):
        assess([], pd.DataFrame(), min_contracts=0)
