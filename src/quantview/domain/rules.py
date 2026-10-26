from __future__ import annotations

import re
from datetime import date

from quantview.domain.models import Instrument
from quantview.operations.clock import shanghai_now

CONTRACT_PATTERN = re.compile(r"^([A-Za-z]+)(\d{3,4})$")


def parse_contract_code(
    contract_code: str,
    *,
    exchange: str = "DCE",
    reference_date: date | None = None,
    multiplier: float | None = None,
    price_tick: float | None = None,
) -> Instrument:
    """Parse a DCE contract into a stable identity.

    Four digit suffixes are YYMM. Three digit suffixes are interpreted using the
    nearest non-past decade around reference_date and kept only for defensive
    compatibility with upstream formatting.
    """
    normalized = contract_code.strip().upper().replace(".DCE", "")
    match = CONTRACT_PATTERN.fullmatch(normalized)
    if not match:
        raise ValueError(f"Invalid futures contract code: {contract_code!r}")
    product, suffix = match.groups()
    month = int(suffix[-2:])
    if month < 1 or month > 12:
        raise ValueError(f"Invalid delivery month in contract code: {contract_code!r}")
    if len(suffix) == 4:
        year = 2000 + int(suffix[:2])
    else:
        ref_year = (reference_date or shanghai_now().date()).year
        decade = (ref_year // 10) * 10
        candidates = [decade - 10 + int(suffix[0]), decade + int(suffix[0]), decade + 10 + int(suffix[0])]
        year = min(candidates, key=lambda value: abs(value - ref_year))
    instrument_id = f"{exchange}.{product}.{year % 100:02d}{month:02d}"
    return Instrument(
        instrument_id=instrument_id,
        exchange=exchange,
        product=product,
        contract_code=f"{product}{year % 100:02d}{month:02d}",
        delivery_year=year,
        delivery_month=month,
        source_symbol=normalized,
        contract_multiplier=multiplier,
        price_tick=price_tick,
    )


def delivery_ordinal(instrument: Instrument) -> int:
    return instrument.delivery_year * 12 + instrument.delivery_month

