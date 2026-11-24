from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from quantview.metals.models import MetalsSettings, fund_asset
from quantview.metals.service import MetalsService, gold_silver_ratio, indicators
from quantview.operations.clock import shanghai_now
from quantview.ui.data_status import render_market_status
from quantview.ui.market_terminal import (
    load_market,
    render_asset_detail,
    render_comparison,
    render_watch_table,
)
from quantview.ui.market_update import render_job_status, update_button

METALS = {"gold": "黄金", "silver": "白银", "unknown": "未确认"}


def parse_watchlist(text: str) -> list[dict]:
    result = []
    metals = {"黄金": "gold", "白银": "silver", "未确认": "unknown"}
    for index, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.replace("，", ",").split(",")]
        if len(parts) != 3:
            raise ValueError(f"第 {index} 行需填写：代码, ETF或LOF, 黄金或白银或未确认。")
        code, kind, metal = parts
        asset = fund_asset(code, kind.upper(), metals.get(metal, metal.lower()))
        result.append({"code": asset.symbol, "kind": asset.kind, "metal": asset.metal})
    return result


def _percent(value):
    return f"{value:+.2%}" if value is not None else "—"


def _card(asset, snapshot, attempt, settings):
    with st.container(border=True):
        name = snapshot["verification"]["name"] if snapshot else asset.name
        st.markdown(f"### {name}")
        identity = f"{asset.market} · {asset.symbol}" + (f" · {asset.kind}" if asset.kind != "SGE" else "")
        st.caption(f"{identity} · {asset.price_unit}")
        if attempt and attempt["status"] == "failed":
            st.warning(f"最近更新失败：{attempt['error']}")
        if not snapshot:
            st.metric("日线收盘参考价", "—")
            st.info("尚无已核验缓存。点击“更新公开行情”采集；无数据不会填零。")
            return
        latest = snapshot["quotes"][-1]
        st.metric("日线收盘参考价", f"{latest['close']:,.3f}")
        st.write(f"行情截至 **{latest['trade_date']}**")
        age = (shanghai_now().date() - pd.Timestamp(latest["trade_date"]).date()).days
        if age > settings.stale_after_calendar_days:
            st.warning(f"已距今 {age} 个自然日，超过本地陈旧阈值；请核对来源与休市情况。")
        metrics = indicators(snapshot)
        st.write(f"5 个观测变化 **{_percent(metrics['change_5'])}** · 20 个观测变化 **{_percent(metrics['change_20'])}**")
        if snapshot["quality"]["rejected_rows"]:
            st.warning(f"本批剔除 {len(snapshot['quality']['rejected_rows'])} 条无效收盘价；变化按有效观测计算。")
        fetched = pd.Timestamp(snapshot["fetched_at"]).strftime("%Y-%m-%d %H:%M")
        st.caption(f"获取：{fetched}（上海） · AKShare / {'上金所' if asset.kind == 'SGE' else '新浪财经'}")
        if asset.kind != "SGE":
            st.caption(f"{asset.kind} 类别由行情目录核验；关注金属“{METALS[asset.metal]}”是用户标记，尚未核验基金合同和跟踪标的。")


def render_metals(settings: MetalsSettings) -> None:
    service = MetalsService(settings)
    st.caption("QUANTVIEW / PRECIOUS METALS")
    header, action = st.columns([4, 1])
    header.title("贵金属工作台")
    with action:
        update_button(service, "更新公开行情", "metals_sync")
    st.caption("黄金 · 白银 / 国内现货、基金自选与来源证据")
    st.sidebar.caption("公开行情与农产品分开更新；打开页面只读本地缓存。")
    if st.sidebar.button("刷新贵金属缓存", key="metals_refresh"):
        st.rerun()
    render_job_status(service)

    try:
        assets = service.assets()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        st.error(f"无法读取基金自选：{exc}")
        return
    snapshots, attempts = load_market(service, assets)

    render_market_status([
        {"行情日期": snapshots[a.asset_id]["quotes"][-1]["trade_date"] if snapshots[a.asset_id] else None}
        for a in assets
    ], stale_after=settings.stale_after_calendar_days, compact=True)

    market, funds, channels = st.tabs(["市场观察", "基金自选", "积存金与实物"])
    with market:
        benchmarks = [asset for asset in assets if asset.kind == "SGE"]
        summary = st.columns(len(benchmarks) + 1)
        for column, asset in zip(summary[:-1], benchmarks, strict=True):
            snapshot = snapshots[asset.asset_id]
            quotes = snapshot["quotes"] if snapshot else []
            change = quotes[-1]["close"] / quotes[-2]["close"] - 1 if len(quotes) > 1 else None
            column.metric(f"{asset.symbol} · {asset.price_unit}", f"{quotes[-1]['close']:,.3f}" if quotes else "—", _percent(change), delta_color="inverse")
            column.caption(f"{quotes[-1]['trade_date']} · {asset.name}" if quotes else "尚无缓存")
            attempt = attempts.get(asset.asset_id)
            if attempt and attempt["status"] == "failed":
                column.warning(f"{asset.symbol} 最近更新失败：{attempt['error']}")
        ratio, note = gold_silver_ratio(snapshots.get("sge_Au9999"), snapshots.get("sge_Ag9999"))
        summary[-1].metric("金银比 · 同日现货收盘参考", f"{ratio:.2f}" if ratio is not None else "—")
        with st.expander("金银比口径与日线时间说明"):
            st.caption(note)
            st.caption("当日来源数据在上海时间 18:10 前不展示。交易日历未核验，新鲜度按自然日提示；价格变化均非个人投资收益。")
        render_watch_table(assets, snapshots, attempts, settings, prefix="metals")
        render_asset_detail(assets, snapshots, attempts, prefix="metals")
        with st.expander("多标的走势与相关矩阵"):
            render_comparison(assets, snapshots, prefix="metals")

    with funds:
        st.subheader("你的基金观察清单")
        st.write("添加实际基金代码并选择 ETF 或 LOF。保存自选只记录关注对象，更新时再通过行情目录核验产品类别。")
        fund_assets = [asset for asset in assets if asset.kind != "SGE"]
        if not fund_assets:
            st.info("尚未登记具体产品，不预设你持有任何基金。")
        for asset in fund_assets:
            _card(asset, snapshots[asset.asset_id], attempts[asset.asset_id], settings)
        default = "\n".join(f"{a.symbol}, {a.kind}, {METALS[a.metal]}" for a in fund_assets)
        if "metals_watchlist_base" not in st.session_state:
            st.session_state["metals_watchlist_base"] = [
                {"code": a.symbol, "kind": a.kind, "metal": a.metal} for a in fund_assets
            ]
        if st.button("重新载入已保存清单", key="metals_watchlist_reload"):
            st.session_state["metals_watchlist_text"] = default
            st.session_state["metals_watchlist_base"] = [
                {"code": a.symbol, "kind": a.kind, "metal": a.metal} for a in fund_assets
            ]
        with st.form("metals_watchlist_form"):
            text = st.text_area("每行：代码, ETF或LOF, 黄金或白银或未确认", value=default, placeholder="填写实际持有或关注的产品；最多 10 只", height=150, key="metals_watchlist_text")
            st.caption("以本次列表替换全部基金自选；清空并保存可移除自选，已采集的历史行情版本仍保留。")
            if st.form_submit_button("保存基金自选", key="metals_watchlist_save"):
                try:
                    rows = parse_watchlist(text)
                    service.save_watchlist(rows, expected=st.session_state["metals_watchlist_base"])
                    st.session_state["metals_watchlist_base"] = rows
                    st.session_state["metals_watchlist_saved"] = True
                    st.rerun()
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
        if st.session_state.pop("metals_watchlist_saved", False):
            st.success("基金自选已保存。点击“更新公开行情”获取新加入产品。")
        st.caption("首版提供场内价格观察。净值、折溢价、合同基准、个人份额、成本和交易账本尚未接入；不能从名称推断实物含银量。")

    with channels:
        st.subheader("积存金与实物：接下来记录什么")
        st.write("市场行情已可观察。个人余额和批次登记尚未开放，当前页面不计算你的资产总额或收益。")
        st.dataframe(pd.DataFrame([
            {"渠道": "银行积存金", "需要的资料": "银行、产品名、余额单位、报价日期、用户赎回价、费用规则", "估值要点": "使用该产品适用的赎回报价；未知费用不能按零处理"},
            {"渠道": "实物金银", "需要的资料": "批次、品类、毛重、成色、成本、适用回购报价与费用", "估值要点": "材料参考价值与参考变现金额分开；工费不随金价自动升值"},
            {"渠道": "积存兑换实物", "需要的资料": "转出余额、转入实物、兑换日期与费用", "估值要点": "同一份金属只计一次，防止积存余额与实物重复合计"},
        ]), hide_index=True, width="stretch")
        st.caption("无需把金额或银行凭证发到聊天。本页只采集公开行情，自选留在本地，不调用 AI、不发送邮件。")
    with st.expander("采集状态"):
        st.json({"calendar_coverage": "未核验", "lookback_calendar_days": settings.lookback_days, "attempts": attempts})
        result = st.session_state.get("metals_sync_result")
        if result:
            st.code(json.dumps(result, ensure_ascii=False, indent=2), language="json")
