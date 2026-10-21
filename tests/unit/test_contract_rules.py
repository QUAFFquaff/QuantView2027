from datetime import date

import pytest

from quantview.domain.rules import parse_contract_code


def test_parse_four_digit_dce_contract():
    instrument = parse_contract_code("lh2601", reference_date=date(2026, 10, 20))
    assert instrument.instrument_id == "DCE.LH.2601"
    assert instrument.product == "LH"
    assert instrument.delivery_year == 2026
    assert instrument.delivery_month == 1


def test_parse_three_digit_contract_uses_nearest_decade():
    instrument = parse_contract_code("C701", reference_date=date(2026, 10, 20))
    assert instrument.delivery_year == 2027
    assert instrument.contract_code == "C2701"


@pytest.mark.parametrize("symbol", ["LH2613", "2601", "LH-2601", ""])
def test_invalid_contract_codes_are_rejected(symbol):
    with pytest.raises(ValueError):
        parse_contract_code(symbol)

