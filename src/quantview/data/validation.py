from __future__ import annotations

from dataclasses import replace

from quantview.domain.models import DailyBar


def validate_bar(bar: DailyBar) -> DailyBar:
    errors: list[str] = []
    warnings = list(bar.quality_flags)
    if any(value <= 0 for value in (bar.open, bar.high, bar.low, bar.close, bar.settlement)):
        errors.append("non_positive_price")
    if not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high):
        errors.append("ohlc_inconsistent")
    if bar.volume < 0:
        errors.append("negative_volume")
    if bar.open_interest < 0:
        errors.append("negative_open_interest")
    if bar.volume == 0 and bar.open_interest > 0:
        warnings.append("zero_volume_with_open_interest")
    flags = tuple(dict.fromkeys([*errors, *warnings]))
    status = "rejected" if errors else ("warning" if warnings else "ok")
    return replace(bar, quality_status=status, quality_flags=flags)


def validate_bars(bars: list[DailyBar]) -> tuple[list[DailyBar], list[DailyBar]]:
    accepted: list[DailyBar] = []
    rejected: list[DailyBar] = []
    seen: set[tuple] = set()
    for raw in bars:
        bar = validate_bar(raw)
        key = (bar.trade_date, bar.instrument_id)
        if key in seen:
            bar = replace(
                bar,
                quality_status="rejected",
                quality_flags=tuple(dict.fromkeys([*bar.quality_flags, "duplicate_primary_key"])),
            )
        seen.add(key)
        (rejected if bar.quality_status == "rejected" else accepted).append(bar)
    return accepted, rejected

