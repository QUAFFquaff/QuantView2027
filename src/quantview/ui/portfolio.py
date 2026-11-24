from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from quantview.ui.charts import style


def render_portfolio(view):
    total = float(view["total"])
    funds = sorted(view["funds"], key=lambda row: -row["value"])
    st.caption(f"估值时点 {view['as_of']:%Y-%m-%d %H:%M}（上海） · 证券市值口径，不含现金")
    columns = st.columns(5)
    columns[0].metric("快照证券总市值（元）", f"{total:,.2f}")
    columns[1].metric("已列基金数量", str(len(funds)))
    columns[2].metric("已列基金占证券市值", f"{float(view['tracked']) / total:.1%}")
    columns[3].metric("最大已列单只权重", f"{float(funds[0]['value']) / total:.1%}" if funds else "—")
    columns[4].metric("前三已列基金权重", f"{sum(float(row['value']) for row in funds[:3]) / total:.1%}" if funds else "—")
    left, right = st.columns([1, 1.35])
    groups = {name: float(value) for name, value in view["groups"].items()}
    if view["outside"]:
        groups["未纳入本视图"] = float(view["outside"])
    with left:
        st.markdown("**产品暴露分布**")
        figure = go.Figure(go.Pie(labels=list(groups), values=list(groups.values()), hole=0.66,
                                  textinfo="percent", sort=False, marker={"line": {"color": "#0f141c", "width": 3}},
                                  hovertemplate="%{label}<br>市值 %{value:,.2f} 元<br>%{percent}<extra></extra>"))
        style(figure, "", "")
        figure.update_layout(height=310, margin={"l": 8, "r": 8, "t": 8, "b": 10}, hovermode="closest", legend={"y": -0.05, "orientation": "h"})
        st.plotly_chart(figure, width="stretch", key="portfolio_allocation")
    with right:
        st.markdown("**已列基金权重 · 占证券总市值**")
        selected = funds[:12][::-1]
        figure = go.Figure(go.Bar(x=[float(row["value"]) / total * 100 for row in selected],
                                  y=[f"{row['name']} · {row['code']}" for row in selected], orientation="h", marker_color="#66c5d5",
                                  hovertemplate="%{y}<br>证券市值占比 %{x:.2f}%<extra></extra>"))
        style(figure, "", "证券市值占比（%）")
        figure.update_layout(height=310, margin={"l": 8, "r": 8, "t": 8, "b": 10}, xaxis_title="证券市值占比（%）", yaxis_title=None)
        st.plotly_chart(figure, width="stretch", key="portfolio_weights")
        if len(funds) > 12:
            st.caption("图示权重最大的 12 只；全部持仓见下表。")
    coverage = st.columns(3)
    coverage[0].metric("已披露成分占证券市值", f"{float(view['known']) / total:.1%}")
    coverage[1].metric("已列基金未穿透部分", f"{float(view['tracked'] - view['known']) / total:.1%}")
    coverage[2].metric("未列证券部分", f"{float(view['outside']) / total:.1%}")
    st.caption("上方三项合计为证券总市值。未穿透部分包括不适用股票穿透的工具；集中度仅针对已列基金，不代表底层行业集中度或全部资产风险。")
    if view["outside"]:
        st.info("仍有证券市值未列入基金明细；补齐快照后，可更完整地判断产品配置与集中度。")
    frame = pd.DataFrame([{"产品暴露": name, "证券市值（元）": value, "权重（%）": value / total * 100} for name, value in groups.items()])
    with st.expander("查看配置明细"):
        st.dataframe(frame.sort_values("证券市值（元）", ascending=False), hide_index=True, width="stretch", column_config={
            "证券市值（元）": st.column_config.NumberColumn(format="%.2f"),
            "权重（%）": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.2f%%"),
        })
