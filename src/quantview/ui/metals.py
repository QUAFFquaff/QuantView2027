from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from quantview.metals.models import MetalsSettings, fund_asset
from quantview.metals.service import MetalsService, gold_silver_ratio, indicators
from quantview.operations.clock import shanghai_now
from quantview.ui.charts import style

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
        st.caption(f"最近 60 个观测最大回撤 {_percent(metrics['drawdown_60'])} · 样本 {metrics['drawdown_count']}/60")
        if snapshot["quality"]["rejected_rows"]:
            st.warning(f"本批剔除 {len(snapshot['quality']['rejected_rows'])} 条无效收盘价；变化按有效观测计算。")
        fetched = pd.Timestamp(snapshot["fetched_at"]).strftime("%Y-%m-%d %H:%M")
        st.caption(f"获取：{fetched}（上海时间） · 来源：AKShare / {'上金所' if asset.kind == 'SGE' else '新浪财经'}")
        st.markdown(f"[查看来源]({asset.source_url})")
        if asset.kind != "SGE":
            st.caption(f"{asset.kind} 类别由行情目录核验；关注金属“{METALS[asset.metal]}”是用户标记，尚未核验基金合同和跟踪标的。")


def render_metals(settings: MetalsSettings) -> None:
    service = MetalsService(settings)
    st.caption("QUANTVIEW / PRECIOUS METALS")
    st.title("贵金属工作台")
    st.caption("黄金 · 白银 / 国内现货、基金自选与来源证据")
    st.sidebar.caption("公开行情与农产品分开更新；打开页面只读本地缓存。")
    if st.sidebar.button("刷新贵金属缓存", key="metals_refresh"):
        st.rerun()
    if st.button("更新公开行情", key="metals_sync", type="primary"):
        try:
            with st.status("正在采集公开行情…", expanded=True) as progress:
                result = service.sync(on_progress=lambda asset: st.write(f"采集 {asset.symbol} · 最长 {settings.timeout_seconds} 秒"))
                failures = [row for row in result["results"] if row["status"] == "failed"]
                progress.update(label="行情更新完成" if not failures else "部分或全部来源未成功，保留旧数据", state="complete" if not failures else "error")
            st.session_state["metals_sync_result"] = result
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    st.caption("日线参考，非实时；当日数据在上海时间 18:10 前暂不展示。各市场交易日历尚未接入，新鲜度按自然日提示。价格变化和回撤均非个人投资收益。")

    try:
        assets = service.assets()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        st.error(f"无法读取基金自选：{exc}")
        return
    snapshots, attempts = {}, {}
    for asset in assets:
        try:
            snapshots[asset.asset_id] = service.latest(asset)
            attempts[asset.asset_id] = service.attempt(asset)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            snapshots[asset.asset_id] = None
            attempts[asset.asset_id] = {"status": "failed", "error": str(exc)}

    market, funds, channels = st.tabs(["市场观察", "基金自选", "积存金与实物"])
    with market:
        benchmarks = [asset for asset in assets if asset.kind == "SGE"]
        for column, asset in zip(st.columns(len(benchmarks)), benchmarks, strict=True):
            with column:
                _card(asset, snapshots[asset.asset_id], attempts[asset.asset_id], settings)
        ratio, note = gold_silver_ratio(snapshots.get("sge_Au9999"), snapshots.get("sge_Ag9999"))
        st.metric("金银比 · 同日现货收盘参考", f"{ratio:.2f}" if ratio is not None else "—")
        st.caption(note)
        ready = [asset for asset in assets if snapshots[asset.asset_id]]
        st.subheader("价格与窗口变化")
        if ready:
            chosen = st.selectbox("观察标的", ready, format_func=lambda a: f"{snapshots[a.asset_id]['verification']['name']} · {a.symbol} ({a.price_unit})", key="metals_chart_asset")
            snapshot = snapshots[chosen.asset_id]
            frame = pd.DataFrame(snapshot["quotes"])
            window = st.radio("观测范围", ["最近 60 个观测", "全部缓存"], horizontal=True, key="metals_chart_window")
            shown = frame.tail(60) if window == "最近 60 个观测" else frame
            figure = go.Figure(go.Scatter(x=shown["trade_date"], y=shown["close"], mode="lines", name="收盘参考价", line={"color": "#b8872e", "width": 2.5}))
            st.plotly_chart(style(figure, f"{chosen.symbol} · 日线参考", chosen.price_unit), width="stretch")
            st.caption("横轴为来源日期；未核验应有交易日，不据此推断缺失日或连续交易日。基金行情未请求复权，不用于含分红的总回报。")
            with st.expander("行情明细与版本证据"):
                st.dataframe(frame.sort_values("trade_date", ascending=False), hide_index=True, width="stretch")
                st.download_button("下载该版本行情 CSV", frame.to_csv(index=False), f"{chosen.asset_id}_{snapshot['version']}.csv", "text/csv", key="metals_download_quotes")
                st.json({key: value for key, value in snapshot.items() if key not in {"raw_payload", "quotes"}})
        else:
            st.info("尚无行情。点击上方“更新公开行情”后，这里显示趋势和版本证据。")

    with funds:
        st.subheader("你的基金观察清单")
        st.write("添加实际基金代码并选择 ETF 或 LOF。保存自选只记录关注对象，更新时再通过行情目录核验产品类别。")
        fund_assets = [asset for asset in assets if asset.kind != "SGE"]
        if not fund_assets:
            st.info("尚未登记具体产品，不预设你持有任何基金。")
        for asset in fund_assets:
            _card(asset, snapshots[asset.asset_id], attempts[asset.asset_id], settings)
        default = "\n".join(f"{a.symbol}, {a.kind}, {METALS[a.metal]}" for a in fund_assets)
        with st.form("metals_watchlist_form"):
            text = st.text_area("每行：代码, ETF或LOF, 黄金或白银或未确认", value=default, placeholder="填写实际持有或关注的产品；最多 10 只", height=150, key="metals_watchlist_text")
            st.caption("以本次列表替换全部基金自选；清空并保存可移除自选，已采集的历史行情版本仍保留。")
            if st.form_submit_button("保存基金自选", key="metals_watchlist_save"):
                try:
                    service.save_watchlist(parse_watchlist(text))
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
