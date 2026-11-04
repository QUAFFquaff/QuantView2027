"""Verified public product identities for the first watchlist cohort."""
from dataclasses import dataclass


@dataclass(frozen=True)
class FundProfile:
    name: str
    exposure: str
    source: str
    stock: bool = True
    metal: str = "unknown"


STARTERS = {
    "560860": FundProfile("工业有色ETF", "有色／稀有金属股票", "https://www.sse.com.cn/disclosure/fund/announcement/c/new/2026-01-21/560860_20260121_SSDB.pdf"),
    "512400": FundProfile("有色金属ETF", "有色／稀有金属股票", "https://www.sse.com.cn/assortment/fund/list/etfinfo/basic/index.shtml?FUNDID=512400"),
    "562800": FundProfile("稀有金属ETF", "有色／稀有金属股票", "https://www.sse.com.cn/disclosure/fund/announcement/c/new/2026-03-12/562800_20260312_WXTQ.pdf"),
    "159980": FundProfile("有色金属期货ETF", "有色金属期货", "https://static.cninfo.com.cn/finalpage/2026-08-07/1225462056.PDF", stock=False),
    "517520": FundProfile("黄金股ETF", "黄金产业股票", "https://www.sse.com.cn/disclosure/fund/announcement/c/new/2025-12-27/517520_20251227_ERJB.pdf", metal="gold"),
    "518880": FundProfile("黄金ETF", "黄金现货工具", "https://www.sse.com.cn/assortment/fund/list/etfinfo/basic/index.shtml?FUNDID=518880", stock=False, metal="gold"),
    "159920": FundProfile("恒生ETF", "港股宽基股票", "https://www.chinaamc.com/fund/159920/jijingonggao_jg.shtml"),
}


def starter_watchlist() -> list[dict]:
    return [{"code": code, "kind": "ETF", "metal": profile.metal} for code, profile in STARTERS.items()]
