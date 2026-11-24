"""Low-frequency public DCE calendars, immutable local backups and offline restore.

Markets Open restates exchange notices under CC BY 4.0. Its annual holiday
exceptions plus the exchange weekend rule produce a complete natural-day CSV.
This is a daily calendar, not a night-session or settlement calendar.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from quantview.config import Settings
from quantview.data.repository import Repository
from quantview.operations.calendar import import_calendar, load_calendar, natural_days
from quantview.operations.clock import shanghai_now

PROVIDER = "Markets Open (marketsopen.today)"
LICENSE = "CC-BY-4.0"
MAX_BYTES = 2 * 1024 * 1024
HOLIDAYS = {"new-year", "spring-festival", "qingming", "labour-day", "dragon-boat", "mid-autumn", "national-day"}


def source_url(year: int) -> str:
    if type(year) is not int or not 2000 <= year <= 2100:
        raise ValueError("日历年份须在 2000—2100 之间。")
    return f"https://marketsopen.today/data/china-exchange-holidays-{year}.json"


def backup_root(settings: Settings, year: int) -> Path:
    source_url(year)
    return settings.root / "data" / "reference" / "calendars" / "DCE" / str(year)


def download_calendar(year: int) -> bytes:
    request = Request(source_url(year), headers={"User-Agent": "QuantView2027/0.1 calendar-backup", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            content = response.read(MAX_BYTES + 1)
    except (OSError, URLError) as exc:
        raise ValueError(f"公开日历下载失败，原日历与备份保留：{str(exc)[:250]}") from exc
    if len(content) > MAX_BYTES:
        raise ValueError("公开日历超过 2 MiB，未导入。")
    return content


def _day(raw, year: int) -> date:
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ValueError("公开日历含无效日期。")
    result = date.fromisoformat(raw)
    if result.year != year:
        raise ValueError("公开日历含跨年日期，需核对来源后适配。")
    return result


def normalize_calendar(content: bytes, year: int) -> tuple[bytes, dict]:
    source_url(year)
    if len(content) > MAX_BYTES:
        raise ValueError("公开日历超过 2 MiB，未导入。")
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("来源未返回有效日历 JSON，未修改本地日历。") from exc
    if not isinstance(payload, dict) or payload.get("year") != year or payload.get("license") != LICENSE:
        raise ValueError("来源年份或许可信息不匹配，未导入。")
    version = payload.get("data_version")
    attribution = payload.get("attribution")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", version) or not isinstance(attribution, str) or not attribution.strip():
        raise ValueError("来源缺少可追溯版本或署名，未导入。")
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not all(isinstance(row, dict) for row in raw_rows):
        raise ValueError("公开日历 rows 结构变化，未导入。")
    holidays = [row for row in raw_rows if row.get("market_id") == "DCE"]
    identities = [row.get("holiday_id") for row in holidays]
    if not all(isinstance(value, str) and value for value in identities) or not HOLIDAYS <= set(identities) or len(identities) != len(set(identities)):
        raise ValueError(f"公开源未完整载入 {year} 年大商所日历；不按工作日猜测未知年份。")
    days = {day: day.weekday() < 5 for day in natural_days(date(year, 1, 1), date(year, 12, 31))}
    details, closures, reopenings = [], set(), set()
    for row in holidays:
        if row.get("time_zone") != "Asia/Shanghai" or row.get("source_type") != "official" or row.get("kind") not in (None, "holiday"):
            raise ValueError("大商所日历时区、公告依据或日类型变化，未导入。")
        notice = row.get("official_notice_url")
        if not isinstance(notice, str) or urlparse(notice).scheme not in {"http", "https"} or urlparse(notice).hostname not in {"dce.com.cn", "www.dce.com.cn"}:
            raise ValueError("缺少大商所公告原始链接，未导入。")
        start, end, first, last, reopen = (_day(row.get(key), year) for key in ("holiday_start", "holiday_end", "closed_from", "closed_to", "reopens_on"))
        if not first <= start <= end <= last or (last - first).days > 20 or reopen != last + timedelta(days=1) or reopen.weekday() >= 5:
            raise ValueError("休市区间或复市日期不一致，未导入。")
        # Extended boundaries may add weekends, not unexplained working days.
        if any(day.weekday() < 5 for day in natural_days(first, last) if day < start or day > end):
            raise ValueError("来源扩展休市范围含未说明的工作日，未导入。")
        for day in natural_days(first, last):
            if day in closures:
                raise ValueError("公开日历休市区间重叠，需核对后导入。")
            closures.add(day)
            days[day] = False
        reopenings.add(reopen)
        details.append({"name": row.get("holiday_name_zh") or row["holiday_id"], "start": first.isoformat(),
                        "end": last.isoformat(), "reopens_on": reopen.isoformat(), "notice_url": notice,
                        "source_checked_at": row.get("source_checked_at")})
    if reopenings & closures or not 200 <= sum(days.values()) <= 255:
        raise ValueError("年度开市数量或复市日期异常，未导入。")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["trade_date", "is_trading_day"])
    writer.writerows((day.isoformat(), int(flag)) for day, flag in days.items())
    normalized = output.getvalue().encode("utf-8")
    return normalized, {"schema_version": 1, "exchange": "DCE", "year": year, "provider": PROVIDER,
                        "source_url": source_url(year), "data_version": version, "license": LICENSE,
                        "attribution": attribution, "coverage_start": f"{year}-01-01", "coverage_end": f"{year}-12-31",
                        "calendar_days": len(days), "trading_days": sum(days.values()), "holidays": details,
                        "source_sha256": hashlib.sha256(content).hexdigest(), "csv_sha256": hashlib.sha256(normalized).hexdigest(),
                        "basis": "annual_exchange_holidays_and_weekends", "scope": "daily_calendar_only"}


def _atomic(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".calendar-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def _read_version(settings: Settings, year: int, identity: str) -> tuple[bytes, dict]:
    root = backup_root(settings, year)
    try:
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("bad version")
        folder = root / "versions" / identity
        raw = (folder / "source.json").read_bytes()
        normalized, expected = normalize_calendar(raw, year)
        metadata = json.loads((folder / "manifest.json").read_bytes())
        if not isinstance(metadata, dict):
            raise ValueError("invalid metadata")
        if expected["source_sha256"] != identity or any(metadata.get(key) != value for key, value in expected.items()) or (folder / "calendar.csv").read_bytes() != normalized:
            raise ValueError("checksum mismatch")
        fetched_at = datetime.fromisoformat(metadata["fetched_at"])
        if fetched_at.tzinfo is None:
            raise ValueError("missing timezone")
        metadata["backup_directory"] = str(folder)
        return normalized, metadata
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("本地日历备份校验失败，未导入；请保留并检查损坏的备份目录后重新同步。") from exc


def read_backup(settings: Settings, year: int) -> tuple[bytes, dict] | None:
    pointer = backup_root(settings, year) / "latest.json"
    if not pointer.exists():
        return None
    try:
        identity = json.loads(pointer.read_bytes())["version"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("本地日历版本引用损坏；可重新联网校验并发布版本。") from exc
    return _read_version(settings, year, identity)


def _activate(settings: Settings, normalized: bytes, metadata: dict, folder: Path) -> dict:
    repository = Repository(settings.database_path)
    calendar = load_calendar(repository, settings.calendar_exchange)
    rows = {date.fromisoformat(row["trade_date"]): row["is_trading_day"] == "1"
            for row in csv.DictReader(io.StringIO(normalized.decode("utf-8")))}
    source_version = f"{metadata['data_version']}:{metadata['source_sha256'][:16]}"
    if all(day in calendar.days and calendar.days[day] == flag
           and calendar.provenance[day].get("source_version") == source_version
           and calendar.provenance[day].get("file_hash") == metadata["csv_sha256"] for day, flag in rows.items()):
        return {"database_changed": False, "import_id": None}
    result = import_calendar(repository, folder / "calendar.csv", source=f"{PROVIDER} · {metadata['source_url']}",
                             source_version=source_version, verified=True, exchange="DCE")
    return {"database_changed": True, "import_id": result["import_id"]}


@contextmanager
def _sync_lock(settings: Settings):
    root = settings.root / "data" / "reference" / "calendars"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "sync.lock"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError("已有日历同步任务，请稍后重试。") from exc
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(f"pid={os.getpid()} at={shanghai_now().isoformat()}")
        yield
    finally:
        path.unlink(missing_ok=True)


def sync_calendar(settings: Settings, *, year: int | None = None, refresh=False, offline=False, fetcher=None, now: datetime | None = None) -> dict:
    instant = shanghai_now(now)
    year = instant.year if year is None else year
    source_url(year)
    if settings.calendar_exchange != "DCE" or (refresh and offline):
        raise ValueError("仅支持大商所日历；离线恢复与重新联网不能同时选择。")
    with _sync_lock(settings):
        cached = read_backup(settings, year) if not refresh else None
        if cached:
            normalized, metadata = cached
            folder = Path(metadata["backup_directory"])
            activation = _activate(settings, normalized, metadata, folder)
            return {"status": "restored" if activation["database_changed"] else "cached", **metadata, **activation}
        if offline:
            raise ValueError(f"没有 {year} 年本地日历备份，未联网也未修改数据库。")
        raw = (fetcher or download_calendar)(year)
        normalized, metadata = normalize_calendar(raw, year)
        root = backup_root(settings, year)
        folder = root / "versions" / metadata["source_sha256"]
        # Version directories are immutable once a complete backup is published.
        if not (folder / "manifest.json").exists():
            _atomic(folder / "source.json", raw)
            _atomic(folder / "calendar.csv", normalized)
            _atomic(folder / "manifest.json", _json_bytes({**metadata, "fetched_at": instant.isoformat()}))
        else:
            if (folder / "source.json").read_bytes() != raw or (folder / "calendar.csv").read_bytes() != normalized:
                raise ValueError("同版本备份内容已损坏，未覆盖；请先保留并检查该备份目录。")
        # Check the entire immutable version before any database write.
        normalized, saved = _read_version(settings, year, metadata["source_sha256"])
        activation = _activate(settings, normalized, saved, folder)
        _atomic(root / "latest.json", _json_bytes({"version": metadata["source_sha256"]}))
        return {"status": "synced", **saved, **activation}
