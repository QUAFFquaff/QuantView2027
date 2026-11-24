from __future__ import annotations

import pandas as pd
import streamlit as st

from quantview.funds.catalog import starter_watchlist
from quantview.funds.exposure import estimate, load_bundle
from quantview.metals.models import MetalsSettings
from quantview.metals.service import MetalsService
from quantview.ui.data_status import render_market_status
from quantview.ui.fund_import import render_history, render_import
from quantview.ui.fund_watchlist import render_watchlist
from quantview.ui.market_terminal import (
    load_market,
    render_asset_detail,
    render_comparison,
    render_watch_table,
)
from quantview.ui.market_update import render_job_status, update_button
from quantview.ui.portfolio import render_portfolio


def _prices(service: MetalsService) -> None:
    render_watchlist(service)
    assets = service.watchlist()
    left, right = st.columns(2)
    if left.button("追加七只观察样例", key="funds_add_starters"):
        try:
            added = service.append_watchlist(starter_watchlist())
            st.success(f"已追加 {len(added)} 只，原有自选保留。" if added else "七只样例均已在自选中。")
            assets = service.watchlist()
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    with right:
        update_button(service, "更新基金日线", "funds_sync", funds_only=True, disabled=not assets)
    render_job_status(service)
    if not assets:
        st.info("暂无基金自选。请在上方直接添加实际基金，或追加观察样例。")
        return

    snapshots, attempts = load_market(service, assets)
    render_market_status([{"行情日期": snapshots[a.asset_id]["quotes"][-1]["trade_date"] if snapshots[a.asset_id] else None} for a in assets],
                         stale_after=service.settings.stale_after_calendar_days, compact=True)
    render_watch_table(assets, snapshots, attempts, service.settings, prefix="funds")
    render_asset_detail(assets, snapshots, attempts, prefix="funds")
    with st.expander("多标的走势与相关矩阵"):
        render_comparison(assets, snapshots, prefix="funds")


def _exposure(settings: MetalsSettings) -> None:
    st.subheader("证券市值快照与基金穿透")
    st.caption("持仓金额来自同一时点的本地快照，不会用上方最新报价重算；成分权重来自当时已披露的基金报告。")
    try:
        bundle = load_bundle(settings.root)
    except (ValueError, OSError) as exc:
        st.error(f"无法读取暴露快照：{exc}")
        return
    with st.expander("表格录入 / 导入持仓快照", expanded=bundle is None):
        render_import(settings, bundle)
    render_history(settings)
    if bundle is None:
        st.info("尚无私有市值快照；上方行情仍可独立使用。")
        return
    view = estimate(bundle)
    total = view["total"]
    st.caption(f"快照来源：{bundle['source']}")
    render_portfolio(view)

    fund_rows = []
    for fund in view["funds"]:
        report = fund["report"] or {}
        fund_rows.append({
            "基金": f"{fund['name']} {fund['code']}", "产品暴露": fund["category"],
            "快照市值（元）": float(fund["value"]),
            "证券市值权重（%）": float(fund["value"] / total * 100),
            "成分覆盖": f"{fund['known'] / fund['value']:.2%}" if report else None,
            "未穿透金额（元）": float(fund["remaining"]),
            "披露期末": report.get("period_end"), "发布日期": report.get("published_on"),
            "状态": fund["status"], "披露来源": report.get("source_url"),
        })
    if fund_rows:
        st.dataframe(pd.DataFrame(fund_rows), hide_index=True, width="stretch", column_config={
            "披露来源": st.column_config.LinkColumn(display_text="查看报告"),
            "证券市值权重（%）": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.2f%%"),
        })
    st.subheader("已披露成分与重叠")
    if not view["equities"]:
        st.info("没有估值时点前可用的股票持仓披露，无法估算底层持仓。")
        return
    st.caption("只加总同交易所、同证券代码的已披露权重，不将前十大权重重新归一化；不同市场同公司暂不自动合并。底层金额是基金市值的拆分，不另计入资产。")
    overlaps_only = st.checkbox("仅显示由多只基金共同持有的证券", key="funds_overlap_only")
    if overlaps_only and not any(len(item["funds"]) > 1 for item in view["equities"].values()):
        st.info("已披露部分未发现同代码重叠；未披露部分无法判断。")
        return
    st.dataframe(pd.DataFrame([
        {"底层证券": f"{item['name']} · {key}", "估算暴露（元）": float(item["value"]),
         "占证券市值": f"{item['value'] / total:.2%}", "涉及基金数": len(item["funds"]),
         "贡献拆分（元）": "；".join(f"{code} {amount:,.2f}" for code, amount in sorted(item["funds"].items()))}
        for key, item in sorted(view["equities"].items(), key=lambda row: (-row[1]["value"], row[0]))
        if not overlaps_only or len(item["funds"]) > 1
    ]), hide_index=True, width="stretch", column_config={
        "估算暴露（元）": st.column_config.NumberColumn(format="%.2f"),
    })


def render_funds(settings: MetalsSettings) -> None:
    st.caption("QUANTVIEW / PORTFOLIO WORKSPACE")
    st.title("基金投资工作台")
    st.caption("自选行情 / 图表分析 / 持仓配置 / 披露穿透　·　日频参考行情")
    st.sidebar.caption("重新读取只刷新本地文件；采集只在点击“更新基金日线”后执行。")
    if st.sidebar.button("重新读取本地数据", key="funds_refresh"):
        st.rerun()
    prices, positions = st.tabs(["行情与自选", "持仓快照与穿透"])
    with prices:
        try:
            _prices(MetalsService(settings))
        except (ValueError, OSError) as exc:
            st.error(f"基金行情不可用：{exc}")
    with positions:
        _exposure(settings)
