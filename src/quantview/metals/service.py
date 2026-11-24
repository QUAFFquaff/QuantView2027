from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path

from quantview.metals.fetch import fetch_in_worker
from quantview.metals.models import BENCHMARKS, MetalAsset, MetalsSettings, fund_asset
from quantview.operations.clock import shanghai_now

READY_TIME = time(18, 10)  # Conservative local release cutoff; not a verified calendar.


def _content_hash(snapshot: dict) -> str:
    content = {key: value for key, value in snapshot.items() if key not in {"version", "content_hash"}}
    serialized = json.dumps(content, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _read(path: Path, default=None):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (OSError, ValueError) as exc:
        raise ValueError(f"本地贵金属文件损坏，未覆盖：{path.name}") from exc


def _atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _lock(root: Path):
    path = root / "write.lock"
    permission_message = (
        f"无法写入本地数据目录：{root}。当前启动进程缺少写入权限；"
        "请停止当前看板，并在正常 Windows PowerShell 中运行 "
        r".\scripts\start_ui.ps1 重新启动。已有行情不会因本次锁创建失败而被修改。"
    )
    try:
        root.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise ValueError(permission_message) from exc
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("贵金属数据正在更新，请稍后重试；若进程异常退出，确认无更新任务后再移除 write.lock。") from exc
    except PermissionError as exc:
        raise ValueError(permission_message) from exc
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(f"pid={os.getpid()} at={shanghai_now().isoformat()}")
        yield
    finally:
        try:
            path.unlink(missing_ok=True)
        except PermissionError as exc:
            raise ValueError(
                f"更新结束后无法释放锁：{path}。部分文件可能已更新；请先停止看板，"
                "确认没有更新任务，再检查目录权限并正常重新启动。"
            ) from exc


def normalize(payload: dict, asset: MetalAsset, now: datetime, lookback_days: int) -> dict:
    rows, rejected, deferred = {}, [], []
    for raw in payload["rows"]:
        day = date.fromisoformat(str(raw["date"]))
        if day > now.date():
            raise ValueError("来源含未来日期，整批拒绝。")
        if day == now.date() and now.time() < READY_TIME:
            deferred.append(day.isoformat())
            continue
        if (now.date() - day).days > lookback_days:
            continue
        close = raw.get("close")
        if isinstance(close, bool) or not isinstance(close, (float, int)) or not math.isfinite(close) or close <= 0:
            rejected.append({"trade_date": day.isoformat(), "reason": "invalid_close"})
            continue
        key = day.isoformat()
        if key in rows and rows[key]["close"] != float(close):
            raise ValueError("同一日期存在冲突收盘价，整批拒绝。")
        rows[key] = {"trade_date": key, "close": float(close)}
    if not rows:
        raise ValueError("所选回溯区间没有有效正数收盘价。")
    verification = payload["verification"]
    if asset.kind != "SGE" and (verification.get("status") != "provider_category_verified" or verification.get("category") != asset.kind):
        raise ValueError("基金类型未通过来源目录核验。")
    fetched_at = now.isoformat()
    return {
        "schema_version": 1, "asset": asset.record(), "fetched_at": fetched_at,
        "available_at": fetched_at, "timestamp_precision": "date", "observed_at": None,
        "period": "daily", "session": "provider_daily", "source": "AKShare", "endpoint": payload["endpoint"],
        "verification": verification, "price_basis": "provider_daily_close_no_adjustment_requested",
        "quality": {"calendar_coverage": "unverified", "rejected_rows": rejected, "deferred_unready_dates": deferred, "ready_time_shanghai": READY_TIME.isoformat(timespec="minutes"), "sample_count": len(rows)},
        "quotes": [rows[key] for key in sorted(rows)], "raw_payload": payload,
    }


class MetalsService:
    def __init__(self, settings: MetalsSettings):
        self.settings = settings
        self.root = settings.cache_dir

    def watchlist(self) -> list[MetalAsset]:
        value = _read(self.root / "watchlist.json", {"funds": []})
        try:
            rows = value["funds"]
            if not isinstance(rows, list) or len(rows) > 10:
                raise ValueError("自选格式或数量无效。")
            assets = [fund_asset(row["code"], row["kind"], row["metal"]) for row in rows]
            if len({a.asset_id for a in assets}) != len(assets):
                raise ValueError("自选代码重复。")
            return assets
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("本地基金自选格式损坏，未覆盖原文件。") from exc

    def save_watchlist(self, rows: list[dict], *, expected: list[dict] | None = None) -> None:
        if len(rows) > 10:
            raise ValueError("首版最多保存 10 只基金自选。")
        assets = [fund_asset(row["code"], row["kind"], row["metal"]) for row in rows]
        if len({asset.asset_id for asset in assets}) != len(assets):
            raise ValueError("基金代码重复；同一证券不能同时作为 ETF 和 LOF。")
        with _lock(self.root):
            if expected is not None:
                current = [{"code": a.symbol, "kind": a.kind, "metal": a.metal} for a in self.watchlist()]
                if current != expected:
                    raise ValueError("自选已被其他操作修改，请刷新清单后重试；输入已保留。")
            _atomic(self.root / "watchlist.json", {"schema_version": 1, "saved_at": shanghai_now().isoformat(), "funds": rows})

    def append_watchlist(self, additions: list[dict]) -> list[str]:
        """Add missing codes without replacing a user's saved classifications."""
        candidates = [fund_asset(row["code"], row["kind"], row["metal"]) for row in additions]
        with _lock(self.root):
            assets = self.watchlist()
            known = {asset.asset_id for asset in assets}
            added = []
            for asset in candidates:
                if asset.asset_id not in known:
                    assets.append(asset)
                    known.add(asset.asset_id)
                    added.append(asset.symbol)
            if len(assets) > 10:
                raise ValueError("追加后超过 10 只自选，未修改清单；请先调整基金自选。")
            if added:
                rows = [{"code": asset.symbol, "kind": asset.kind, "metal": asset.metal} for asset in assets]
                _atomic(self.root / "watchlist.json", {"schema_version": 1, "saved_at": shanghai_now().isoformat(), "funds": rows})
        return added

    def assets(self) -> list[MetalAsset]:
        return [BENCHMARKS[symbol] for symbol in self.settings.benchmarks] + self.watchlist()

    def latest(self, asset: MetalAsset) -> dict | None:
        pointer = _read(self.root / asset.asset_id / "latest.json")
        if pointer is None:
            return None
        version = pointer.get("version")
        if not isinstance(version, str) or not version.isalnum():
            raise ValueError("行情版本引用无效。")
        snapshot = _read(self.root / asset.asset_id / "versions" / f"{version}.json")
        if snapshot is None:
            raise ValueError("已发布行情版本丢失，请重新更新。")
        if snapshot.get("version") != version or snapshot.get("schema_version") != 1 or snapshot.get("content_hash") != _content_hash(snapshot):
            raise ValueError("行情版本校验失败，拒绝展示；请核对本地文件并重新更新。")
        if snapshot.get("asset") != asset.record():
            # A changed user classification/category needs an explicit new fetch.
            return None
        return snapshot

    def attempt(self, asset: MetalAsset) -> dict | None:
        value = _read(self.root / asset.asset_id / "attempt.json")
        if value is not None and (value.get("status") not in {"success", "failed"} or value.get("asset_id") != asset.asset_id):
            raise ValueError("本地采集状态格式损坏。")
        return value

    def sync(self, *, fetcher=None, now: datetime | None = None, on_progress=None, on_result=None, funds_only: bool = False, asset_ids=None) -> dict:
        fixed_clock = now is not None
        now = now or shanghai_now()
        if now.tzinfo is None:
            raise ValueError("采集时刻必须包含时区。")
        now = now.astimezone(shanghai_now().tzinfo)
        fetcher = fetcher or fetch_in_worker
        results = []
        with _lock(self.root):
            assets = self.watchlist() if funds_only else self.assets()
            if asset_ids is not None:
                requested = set(asset_ids)
                if requested - {asset.asset_id for asset in assets}:
                    raise ValueError("更新对象已不在自选中，请重新读取清单。")
                assets = [asset for asset in assets if asset.asset_id in requested]
            if not assets:
                return {"status": "skipped", "reason": "没有已保存的基金自选", "results": []}
            for asset in assets:
                if on_progress:
                    on_progress(asset)
                attempt = {"asset_id": asset.asset_id, "attempted_at": now.isoformat()}
                try:
                    payload = fetcher(asset, self.settings.lookback_days, now.date(), self.settings.timeout_seconds)
                    completed_at = now if fixed_clock else shanghai_now()
                    snapshot = normalize(payload, asset, completed_at, self.settings.lookback_days)
                    version = uuid.uuid4().hex
                    snapshot.update(version=version, content_hash=_content_hash(snapshot))
                    _atomic(self.root / asset.asset_id / "versions" / f"{version}.json", snapshot)
                    _atomic(self.root / asset.asset_id / "latest.json", {"version": version})
                    attempt.update(status="success", version=version, latest_date=snapshot["quotes"][-1]["trade_date"], sample_count=len(snapshot["quotes"]))
                except Exception as exc:
                    attempt.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:600]}")
                _atomic(self.root / asset.asset_id / "attempt.json", attempt)
                results.append(attempt)
                if on_result:
                    on_result(attempt)
        success = sum(item["status"] == "success" for item in results)
        return {"status": "success" if success == len(results) else "partial" if success else "failed", "results": results}

    def status(self) -> dict:
        result = []
        for asset in self.assets():
            snapshot = self.latest(asset)
            result.append({"asset": asset.record(), "latest_date": snapshot["quotes"][-1]["trade_date"] if snapshot else None, "fetched_at": snapshot["fetched_at"] if snapshot else None, "attempt": self.attempt(asset)})
        return {"assets": result, "calendar_coverage": "unverified"}


def indicators(snapshot: dict) -> dict:
    prices = [row["close"] for row in snapshot["quotes"]]
    tail = prices[-60:]
    peak, max_drawdown = tail[0], 0.0
    for price in tail:
        peak = max(peak, price)
        max_drawdown = min(max_drawdown, price / peak - 1)
    return {"count": len(prices), "change_5": prices[-1] / prices[-6] - 1 if len(prices) >= 6 else None, "change_20": prices[-1] / prices[-21] - 1 if len(prices) >= 21 else None, "drawdown_60": max_drawdown if len(tail) >= 60 else None, "drawdown_count": len(tail)}


def gold_silver_ratio(gold: dict | None, silver: dict | None) -> tuple[float | None, str]:
    if not gold or not silver:
        return None, "金银行情未齐备。"
    left, right = gold["asset"], silver["asset"]
    if (left["symbol"], right["symbol"], left["market"], right["market"]) != ("Au99.99", "Ag99.99", "SGE", "SGE"):
        return None, "只比较已核验的上金所 Au99.99 / Ag99.99 收盘参考价。"
    if left["currency"] != right["currency"] or left["price_unit"] != "元/克" or right["price_unit"] != "元/千克":
        return None, "币种或单位不兼容。"
    g, s = gold["quotes"][-1], silver["quotes"][-1]
    if g["trade_date"] != s["trade_date"]:
        return None, "最新金银行情日期不同，不沿用旧价拼接。"
    return g["close"] * 1000 / s["close"], f"{g['trade_date']} 日线参考：黄金元/克 × 1000 ÷ 白银元/千克；非同步可交易报价，不含税费及成色调整。"
