"""Bounded subprocess boundary for third-party network adapters.

AKShare endpoints do not all expose timeouts. A worker keeps a failed provider
from blocking the UI indefinitely, without changing global HTTP settings.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from quantview.metals.models import MetalAsset


def fetch_quotes(asset: MetalAsset, lookback_days: int, today: date) -> dict:
    import akshare as ak

    verification = {"status": "benchmark", "name": asset.name}
    if asset.kind == "SGE":
        frame = ak.spot_hist_sge(symbol=asset.symbol)
        endpoint = "spot_hist_sge"
    else:
        # Verify category against the provider directory; never substitute ETF for LOF.
        directory = ak.fund_etf_category_sina(symbol=f"{asset.kind}基金")
        full_symbol = ("sh" if asset.market == "SSE" else "sz") + asset.symbol
        matches = directory.loc[directory["代码"].astype(str).str.lower() == full_symbol]
        if len(matches) != 1:
            raise ValueError(f"行情目录未能唯一确认 {asset.symbol} 为 {asset.kind}；请核对代码和类型。")
        verification = {"status": "provider_category_verified", "symbol": full_symbol, "name": str(matches.iloc[0]["名称"]), "category": asset.kind, "directory_endpoint": "fund_etf_category_sina", "underlying_status": "unverified"}
        # This interface returns exchange daily prices, without requesting adjustment.
        # Keep provider-native OHLC; do not use these for total-return calculations.
        frame = ak.fund_etf_hist_sina(symbol=full_symbol)
        endpoint = "fund_etf_hist_sina"
    required = {"date", "close"}
    if not required <= set(frame.columns):
        raise ValueError(f"{endpoint} 返回字段变化，缺少 date / close。")
    parsed = pd.to_datetime(frame["date"], errors="coerce")
    if parsed.isna().any():
        raise ValueError("行情包含无法解析的日期。")
    frame = frame.loc[parsed.dt.date >= today - timedelta(days=lookback_days)].copy()
    frame["date"] = parsed.loc[frame.index].dt.strftime("%Y-%m-%d")
    # JSON null is intentional: normalization must report invalid price rows.
    records = json.loads(frame.to_json(orient="records"))
    return {"endpoint": endpoint, "adapter_version": getattr(ak, "__version__", "unknown"), "request": {"symbol": asset.symbol, "lookback_days": lookback_days, "as_of_date": today.isoformat()}, "verification": verification, "rows": records}


def fetch_in_worker(asset: MetalAsset, lookback_days: int, today: date, timeout_seconds: int) -> dict:
    env = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [source_root, env.get("PYTHONPATH")]))
    try:
        result = subprocess.run(
            [sys.executable, "-m", "quantview.metals.fetch"],
            input=json.dumps({"asset": asset.record(), "lookback_days": lookback_days, "today": today.isoformat()}),
            capture_output=True, text=True, timeout=timeout_seconds, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"行情请求超过 {timeout_seconds} 秒，保留上次成功数据。") from exc
    if result.returncode != 0:
        raise ValueError("行情采集进程失败；请检查网络及 AKShare 依赖。")
    try:
        # Some providers print progress; only the marked final line is the result.
        payload = json.loads(next(line[9:] for line in reversed(result.stdout.splitlines()) if line.startswith("QV_METAL:")))
    except (ValueError, StopIteration) as exc:
        raise ValueError("行情采集未返回有效结果。") from exc
    if "error" in payload:
        raise ValueError(payload["error"])
    return payload


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        output = fetch_quotes(MetalAsset(**request["asset"]), request["lookback_days"], date.fromisoformat(request["today"]))
    except Exception as exc:
        # Store an actionable type/message, never environment variables or a traceback.
        output = {"error": f"{type(exc).__name__}: {str(exc)[:500]}"}
    print("QV_METAL:" + json.dumps(output, ensure_ascii=False, allow_nan=False))
