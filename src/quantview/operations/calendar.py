from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from quantview.operations.clock import shanghai_now

if TYPE_CHECKING:
    from quantview.data.repository import Repository


class CalendarUnavailable(ValueError):
    pass


def natural_days(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end must be on or after start")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


@dataclass(frozen=True)
class TradingCalendar:
    exchange: str = "DCE"
    days: dict[date, bool] = field(default_factory=dict)
    provenance: dict[date, dict[str, Any]] = field(default_factory=dict)

    def covers(self, start: date, end: date) -> bool:
        return all(day in self.days for day in natural_days(start, end))

    def describe(self, target: date) -> dict[str, Any]:
        known = target in self.days
        ranges: list[dict[str, str]] = []
        for day in sorted(self.days):
            if ranges and date.fromisoformat(ranges[-1]["end"]) + timedelta(days=1) == day:
                ranges[-1]["end"] = day.isoformat()
            else:
                ranges.append({"start": day.isoformat(), "end": day.isoformat()})
        return {
            "status": "verified" if known else "unknown",
            "exchange": self.exchange,
            "target_trade_date": target.isoformat(),
            "is_trading_day": self.days.get(target),
            "coverage_start": min(self.days).isoformat() if self.days else None,
            "coverage_end": max(self.days).isoformat() if self.days else None,
            "coverage_ranges": ranges,
            "reason": None if known else "日历缺失或目标日期不在已核验覆盖范围内",
            **self.provenance.get(target, {}),
        }

    def trading_days(self, start: date, end: date) -> list[date]:
        days = natural_days(start, end)
        missing = [day for day in days if day not in self.days]
        if missing:
            raise CalendarUnavailable(f"日历未覆盖 {missing[0]}，请先导入已核验日历")
        return [day for day in days if self.days[day]]

    def expected_trade_date(
        self, *, now: datetime | None = None, ready_time: time = time(18, 10)
    ) -> date:
        instant = shanghai_now(now)
        today = instant.date()
        if today not in self.days:
            raise CalendarUnavailable("上海当前日期不在已核验日历范围内，无法自动选择目标日")
        cutoff = today if instant.time() >= ready_time else today - timedelta(days=1)
        for day in sorted((day for day in self.days if day <= cutoff), reverse=True):
            if not self.covers(day, today):
                raise CalendarUnavailable("日历存在覆盖缺口，无法自动选择目标日")
            if self.days[day]:
                return day
        raise CalendarUnavailable("已核验日历内没有已到日终采集时间的交易日")


def resolve_target(
    calendar: TradingCalendar,
    requested: date | None,
    *,
    trigger_type: str = "manual",
    now: datetime | None = None,
    ready_time: time = time(18, 10),
) -> dict:
    instant = shanghai_now(now)
    automatic = requested is None
    if automatic:
        try:
            requested = calendar.expected_trade_date(now=instant, ready_time=ready_time)
        except CalendarUnavailable as exc:
            return {"status": "blocked", "allowed": False, "skipped": False,
                    "target_trade_date": None, "reason": str(exc), "as_of": instant.isoformat()}
    state = calendar.describe(requested)
    state.update(allowed=True, skipped=False, automatic=automatic, as_of=instant.isoformat())
    if requested > instant.date():
        state.update(status="blocked", allowed=False, reason="不能采集未来交易日")
    elif state["is_trading_day"] is False:
        state.update(status="closed", allowed=False, skipped=True, reason="已核验日历标记为非交易日")
    elif requested == instant.date() and instant.time() < ready_time:
        state.update(status="blocked", allowed=False, reason=f"尚未到上海时间 {ready_time:%H:%M} 日终采集时间")
    elif state["status"] == "unknown" and trigger_type not in {"manual", "backfill", "rebuild"}:
        state.update(status="blocked", allowed=False, reason="日历未核验，暂停调度或受控补跑；可明确指定日期手动运行")
    return state


def backfill_targets(
    calendar: TradingCalendar, start: date, end: date, *,
    now: datetime | None = None, ready_time: time = time(18, 10),
) -> list[date]:
    targets = calendar.trading_days(start, end)
    instant = shanghai_now(now)
    if end > instant.date():
        raise CalendarUnavailable("补数区间不能包含未来日期")
    for target in targets:
        decision = resolve_target(calendar, target, trigger_type="backfill", now=instant, ready_time=ready_time)
        if not decision["allowed"]:
            raise CalendarUnavailable(decision["reason"])
    return targets


def load_calendar(repository: Repository, exchange: str = "DCE") -> TradingCalendar:
    if not repository.path.exists():
        return TradingCalendar(exchange)
    tables = repository.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    )
    if "exchange_calendar" not in set(tables["table_name"]):
        return TradingCalendar(exchange)
    frame = repository.query(
        """
        SELECT c.trade_date, c.is_trading_day, i.source, i.source_version,
               i.verified_at, i.import_id, i.file_hash
        FROM exchange_calendar c JOIN calendar_import i USING (import_id)
        WHERE c.exchange=? ORDER BY c.trade_date
        """,
        [exchange],
    )
    days, provenance = {}, {}
    for row in frame.to_dict("records"):
        target = row.pop("trade_date").date()
        days[target] = bool(row.pop("is_trading_day"))
        row["verified_at"] = shanghai_now(row["verified_at"]).isoformat()
        provenance[target] = row
    return TradingCalendar(exchange, days, provenance)


def import_calendar(
    repository: Repository,
    path: Path,
    *,
    source: str,
    source_version: str,
    verified: bool = False,
    exchange: str = "DCE",
) -> dict:
    """Import a human-verified, gap-free natural-day calendar atomically.

    The confirmation attests to the file's verification; the application does
    not certify an arbitrary uploaded file as an exchange-issued calendar.
    """
    if not verified:
        raise ValueError("请先核验日历来源与内容，并显式确认 verified")
    if exchange != "DCE":
        raise ValueError("Only the DCE calendar is supported")
    if not source.strip() or not source_version.strip():
        raise ValueError("source and source_version must be non-empty")
    content = path.read_bytes()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if set(reader.fieldnames or []) != {"trade_date", "is_trading_day"} or len(reader.fieldnames) != 2:
        raise ValueError("CSV must contain exactly trade_date,is_trading_day")
    days: dict[date, bool] = {}
    for row in reader:
        raw_date = (row.get("trade_date") or "").strip()
        if None in row or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            raise ValueError("Each calendar row must contain an ISO YYYY-MM-DD date and a boolean")
        target = date.fromisoformat(raw_date)
        raw_bool = (row.get("is_trading_day") or "").strip().lower()
        if raw_bool not in {"0", "1", "true", "false"}:
            raise ValueError(f"Invalid is_trading_day for {target}; use 0/1 or true/false")
        if target in days:
            raise ValueError(f"Duplicate calendar date: {target}")
        days[target] = raw_bool in {"1", "true"}
    if not days:
        raise ValueError("Calendar file is empty")
    start, end = min(days), max(days)
    if len(days) != (end - start).days + 1:
        raise ValueError("Calendar must include every natural day, including non-trading days")

    repository.initialize()
    import_id = uuid.uuid4().hex
    verified_at = shanghai_now()
    file_hash = hashlib.sha256(content).hexdigest()
    with repository.connect() as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(
                "INSERT INTO calendar_import VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [import_id, exchange, source.strip(), source_version.strip(), verified_at,
                 start, end, file_hash, len(days)],
            )
            connection.execute(
                "DELETE FROM exchange_calendar WHERE exchange=? AND trade_date BETWEEN ? AND ?",
                [exchange, start, end],
            )
            connection.executemany(
                "INSERT INTO exchange_calendar VALUES (?, ?, ?, ?)",
                [(exchange, target, flag, import_id) for target, flag in sorted(days.items())],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    return {
        "import_id": import_id, "exchange": exchange, "source": source.strip(),
        "source_version": source_version.strip(), "verified_at": verified_at.isoformat(),
        "coverage_start": start.isoformat(), "coverage_end": end.isoformat(),
        "rows": len(days), "file_hash": file_hash,
    }
