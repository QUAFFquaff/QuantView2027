from datetime import UTC, date, datetime

from quantview.data.validation import validate_bar, validate_bars
from quantview.domain.models import DailyBar


def bar(**overrides):
    values = {
        "trade_date": date(2026, 10, 20),
        "instrument_id": "DCE.LH.2701",
        "open": 15000.0,
        "high": 15200.0,
        "low": 14900.0,
        "close": 15100.0,
        "settlement": 15080.0,
        "volume": 1000,
        "open_interest": 5000,
        "source": "fixture",
        "source_fetched_at": datetime.now(UTC),
        "raw_batch_id": "test",
    }
    values.update(overrides)
    return DailyBar(**values)


def test_inconsistent_ohlc_is_rejected():
    result = validate_bar(bar(high=15050, close=15100))
    assert result.quality_status == "rejected"
    assert "ohlc_inconsistent" in result.quality_flags


def test_zero_volume_with_open_interest_is_warning():
    result = validate_bar(bar(volume=0))
    assert result.quality_status == "warning"
    assert "zero_volume_with_open_interest" in result.quality_flags


def test_duplicate_primary_key_is_rejected():
    accepted, rejected = validate_bars([bar(), bar(raw_batch_id="second")])
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert "duplicate_primary_key" in rejected[0].quality_flags

