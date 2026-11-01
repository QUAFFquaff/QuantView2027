from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class MetalAsset:
    asset_id: str
    symbol: str
    name: str
    kind: str
    metal: str
    market: str
    currency: str
    price_unit: str
    source_url: str
    quote_type: str = "daily_close"
    timezone: str = "Asia/Shanghai"

    def record(self) -> dict:
        return asdict(self)


BENCHMARKS = {
    "Au99.99": MetalAsset("sge_Au9999", "Au99.99", "上金所黄金现货", "SGE", "gold", "SGE", "CNY", "元/克", "https://www.sge.com.cn/sjzx/mrhq"),
    "Ag99.99": MetalAsset("sge_Ag9999", "Ag99.99", "上金所白银现货", "SGE", "silver", "SGE", "CNY", "元/千克", "https://www.sge.com.cn/sjzx/mrhq"),
}


def fund_asset(code: str, kind: str, metal: str) -> MetalAsset:
    if not isinstance(code, str):
        raise ValueError("基金代码须为六位文本，以保留完整代码。")
    code = code.strip()
    if not re.fullmatch(r"[15]\d{5}", code):
        raise ValueError("请输入沪深场内基金的六位代码（1 或 5 开头）。")
    if kind not in {"ETF", "LOF"} or metal not in {"gold", "silver", "unknown"}:
        raise ValueError("基金类型或关注金属无效。")
    market = "SSE" if code.startswith("5") else "SZSE"
    return MetalAsset(f"{market}_{code}", code, code, kind, metal, market, "CNY", "元/份", "https://finance.sina.com.cn/fund/")


@dataclass(frozen=True)
class MetalsSettings:
    root: Path
    lookback_days: int = 400
    timeout_seconds: int = 40
    stale_after_calendar_days: int = 7
    benchmarks: tuple[str, ...] = ("Au99.99", "Ag99.99")

    @property
    def cache_dir(self) -> Path:
        return self.root / "data" / "market" / "metals"


def load_metals_settings(root: Path) -> MetalsSettings:
    path = root / "config" / "metals.yaml"
    values = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    values = values or {}
    allowed = {"lookback_days", "timeout_seconds", "stale_after_calendar_days", "benchmarks"}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError("贵金属配置有未知字段。")
    for key, low, high in [("lookback_days", 30, 3650), ("timeout_seconds", 5, 120), ("stale_after_calendar_days", 1, 30)]:
        if key in values and (type(values[key]) is not int or not low <= values[key] <= high):
            raise ValueError(f"贵金属配置 {key} 必须在 {low}–{high} 之间。")
    symbols = values.get("benchmarks", list(BENCHMARKS))
    if not isinstance(symbols, list) or not symbols or any(not isinstance(s, str) or s not in BENCHMARKS for s in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError("贵金属基准仅支持不重复的 Au99.99 / Ag99.99。")
    return MetalsSettings(root=root, **{**values, "benchmarks": tuple(symbols)})
