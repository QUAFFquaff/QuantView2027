from __future__ import annotations

import pandas as pd
import streamlit as st

from quantview.funds.catalog import STARTERS
from quantview.metals.models import fund_asset

METALS = {"未分类": "unknown", "黄金": "gold", "白银": "silver"}


def render_watchlist(service):
    assets = service.watchlist()
    rows = [{"code": a.symbol, "kind": a.kind, "metal": a.metal} for a in assets]
    with st.expander(f"管理基金自选 · {len(assets)} / 10", expanded=not assets):
        st.caption("直接添加、修改类别或移除自选；保存只修改本地观察清单，不登记持仓，也不自动联网。")
        def pick_sample():
            chosen = st.session_state.get("fund_pick_sample")
            if chosen:
                st.session_state["fund_watch_code"] = chosen
        st.selectbox("从观察样例选择（可输入名称或代码搜索）", [None, *STARTERS],
                      format_func=lambda c: f"{c} · {STARTERS[c].name}" if c else "手工输入代码",
                      key="fund_pick_sample", on_change=pick_sample)
        with st.form("fund_watchlist_add_form"):
            code = st.text_input("基金代码", max_chars=6, key="fund_watch_code")
            left, right = st.columns(2)
            kind = left.selectbox("基金类别", ["ETF", "LOF"], key="fund_watch_kind")
            metal = right.selectbox("关注分类（可选）", list(METALS), key="fund_watch_metal")
            st.caption("再次填写同一代码可更新类别或关注分类；产品类型在明确采集时核验。")
            if st.form_submit_button("添加或更新自选", key="fund_watch_add"):
                try:
                    asset = fund_asset(code, kind, METALS[metal])
                    replacement = {"code": asset.symbol, "kind": asset.kind, "metal": asset.metal}
                    new_rows = [replacement if row["code"] == asset.symbol else row for row in rows]
                    if not any(row["code"] == asset.symbol for row in rows):
                        new_rows.append(replacement)
                    service.save_watchlist(new_rows, expected=rows)
                    st.session_state["fund_watch_message"] = f"{asset.symbol} 自选已保存。"
                    st.rerun()
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
        if assets:
            st.dataframe(pd.DataFrame([
                {"代码": a.symbol, "名称": STARTERS[a.symbol].name if a.symbol in STARTERS else a.symbol,
                 "类别": a.kind, "关注分类": next(k for k, v in METALS.items() if v == a.metal)}
                for a in assets
            ]), hide_index=True, width="stretch")
            remove = st.selectbox("要移除的自选", [a.symbol for a in assets], key="fund_watch_remove_code")
            if st.button("移除所选自选", key="fund_watch_remove"):
                try:
                    service.save_watchlist([row for row in rows if row["code"] != remove], expected=rows)
                    st.session_state["fund_watch_message"] = f"{remove} 已移出自选，已采集历史行情仍保留。"
                    st.rerun()
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
    if st.session_state.pop("fund_watch_message", None) is not None:
        st.success("自选清单已更新，点击“更新基金日线”采集最新行情。")
