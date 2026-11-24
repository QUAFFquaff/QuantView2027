"""Preview-first UI for private point-in-time holdings, never an order form."""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import streamlit as st

from quantview.funds.exposure import (
    TEMPLATE,
    bundle_history,
    bundle_revision,
    parse_bundle,
    save_bundle,
)
from quantview.funds.imports import CODE, CSV_TEMPLATE, VALUE, build_table_bundle, read_table
from quantview.operations.clock import SHANGHAI, shanghai_now


def clear_preview():
    st.session_state.pop("fund_snapshot_preview", None)


def render_import(settings, current):
    st.caption("录入同一时点的基金市值，金额不含现金；这里保存市值快照，尚不计算交易成本或个人收益。")
    if "fund_position_seed" not in st.session_state:
        st.session_state["fund_position_seed"] = pd.DataFrame(
            [{CODE: r["code"], VALUE: str(r["market_value_cny"])} for r in current["positions"]]
            if current else [{CODE: "", VALUE: ""}]
        )
    mode = st.radio("录入方式", ["表格录入 / CSV / Excel", "已有 JSON 快照"], horizontal=True,
                    key="fund_import_mode", on_change=clear_preview)
    candidate = None
    if mode == "表格录入 / CSV / Excel":
        with st.expander("从 CSV / Excel 导入表格"):
            st.download_button("下载持仓 CSV 模板", CSV_TEMPLATE.encode("utf-8-sig"),
                               "fund_positions_template.csv", "text/csv")
            upload = st.file_uploader("选择 CSV 或 XLSX（最大 1 MiB）", type=["csv", "xlsx"],
                                      key="fund_position_file", on_change=clear_preview)
            st.caption("Excel 读取第一个工作表，最多 500 行；先将公式粘贴为值。导入后可继续在下表编辑。")
            if upload is not None:
                try:
                    incoming = read_table(upload.getvalue(), upload.name)
                    columns = list(incoming.columns)
                    left, right = st.columns(2)
                    code_col = left.selectbox("基金代码列", columns,
                                               index=columns.index(CODE) if CODE in columns else 0,
                                               key="fund_map_code", on_change=clear_preview)
                    value_col = right.selectbox("基金市值列", columns,
                                                 index=columns.index(VALUE) if VALUE in columns else 1,
                                                 key="fund_map_value", on_change=clear_preview)
                    if st.button("载入表格（不保存）", key="fund_load_table"):
                        if code_col == value_col:
                            raise ValueError("代码和市值必须选择不同的列。")
                        st.session_state["fund_position_seed"] = incoming[[code_col, value_col]].rename(
                            columns={code_col: CODE, value_col: VALUE}).astype(str)
                        st.session_state["fund_editor_generation"] = st.session_state.get("fund_editor_generation", 0) + 1
                        clear_preview()
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
        generation = st.session_state.get("fund_editor_generation", 0)
        frame = st.data_editor(st.session_state["fund_position_seed"], num_rows="dynamic", hide_index=True,
                               width="stretch", key=f"fund_position_editor_{generation}",
                               column_config={CODE: st.column_config.TextColumn(CODE, required=True),
                                              VALUE: st.column_config.TextColumn(VALUE, required=True)},
                               on_change=clear_preview)
        st.caption("代码填六位文本，市值填正数；可在表格中新增或删除行。空白行不计入，重复代码会提示。")
        instant = shanghai_now()
        initial = datetime.fromisoformat(current["as_of"]).astimezone(SHANGHAI) if current else instant
        left, right = st.columns(2)
        day = left.date_input("快照日期（上海）", value=initial.date(), max_value=instant.date(),
                              key="fund_snapshot_date", on_change=clear_preview)
        clock = right.time_input("快照时间（上海）", value=initial.time().replace(second=0, microsecond=0),
                                  key="fund_snapshot_time", on_change=clear_preview)
        total = st.number_input("证券总市值（元，不含现金）", min_value=0.0,
                                 value=float(current["securities_value_cny"]) if current else 0.0,
                                 step=1000.0, key="fund_snapshot_total", on_change=clear_preview)
        source = st.text_input("快照来源", value=current["source"] if current else "手工录入",
                                key="fund_snapshot_source", on_change=clear_preview)
        st.caption("总市值须包含未列出的证券。已有公开成分披露会保留；本次不会按最新行情重估你的历史快照。")
        if st.button("校验并预览持仓", key="fund_preview_table", type="primary"):
            clear_preview()
            try:
                candidate = build_table_bundle(frame, as_of=datetime.combine(day, clock, SHANGHAI),
                                                total=total, source=source,
                                                disclosures=current["disclosures"] if current else [])
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
    else:
        st.download_button("下载 JSON 模板", json.dumps(TEMPLATE, ensure_ascii=False, indent=2),
                           "fund_exposure_template.json", "application/json")
        upload = st.file_uploader("选择快照 JSON（最大 1 MiB）", type="json", key="fund_exposure_file",
                                  on_change=clear_preview)
        st.caption("JSON 可同时导入公开成分披露；仍需预览和确认，不直接覆盖当前快照。")
        if st.button("校验并预览 JSON", disabled=upload is None, key="fund_exposure_save"):
            clear_preview()
            try:
                candidate = parse_bundle(upload.getvalue())
            except (ValueError, TypeError, OSError) as exc:
                st.error(str(exc))
    if candidate is not None:
        st.session_state["fund_snapshot_preview"] = {
            "bundle": candidate, "expected": bundle_revision(current),
        }
    pending = st.session_state.get("fund_snapshot_preview")
    if pending:
        st.subheader("保存前预览")
        bundle = pending["bundle"]
        positions = bundle["positions"]
        subtotal = sum(row["market_value_cny"] for row in positions)
        st.write(f"{bundle['as_of']} · {len(positions)} 只基金 · 已列市值 {subtotal:,.2f} 元")
        st.write(f"未列证券市值：{bundle['securities_value_cny'] - subtotal:,.2f} 元 · 来源：{bundle['source']}")
        st.dataframe(pd.DataFrame(positions).rename(columns={"code": CODE, "market_value_cny": VALUE}),
                     hide_index=True, width="stretch")
        if current:
            st.info("确认后将此版本设为当前快照，原版本保留在历史记录中。")
        if st.button("确认保存持仓快照", key="fund_confirm_snapshot", type="primary"):
            try:
                save_bundle(settings.root, json.dumps(bundle, ensure_ascii=False, allow_nan=False).encode(),
                            expected_revision=pending["expected"])
                clear_preview()
                st.session_state["fund_snapshot_saved"] = True
                st.rerun()
            except (ValueError, OSError) as exc:
                st.error(str(exc))
    if st.session_state.pop("fund_snapshot_saved", False):
        st.success("持仓快照已保存；重复保存相同内容不会增加历史版本。")


def render_history(settings):
    with st.expander("持仓快照历史与导出"):
        try:
            versions = bundle_history(settings.root)
            if not versions:
                st.info("首次通过新入口保存后，这里会保留历史版本。")
                return
            by_revision = {row["revision"]: row["bundle"] for row in versions}
            from quantview.funds.exposure import load_bundle

            current = bundle_revision(load_bundle(settings.root))
            keys = list(by_revision)
            selected = st.selectbox("历史版本", keys, key="fund_history_version",
                                     index=keys.index(current) if current in keys else 0,
                                     format_func=lambda key: f"{'【当前】' if key == current else '历史'} {by_revision[key]['as_of']} · {key[:8]}")
            bundle = by_revision[selected]
            st.dataframe(pd.DataFrame(bundle["positions"]).rename(columns={"code": CODE, "market_value_cny": VALUE}),
                         hide_index=True, width="stretch")
            st.download_button("下载所选历史快照", json.dumps(bundle, ensure_ascii=False, indent=2),
                               f"fund_snapshot_{selected[:12]}.json", "application/json")
            candidates = [None, *[key for key in keys if key != selected]]
            baseline = st.selectbox("对比基准版本", candidates, key="fund_history_baseline",
                                     format_func=lambda key: "选择另一份快照…" if key is None else f"{by_revision[key]['as_of']} · {key[:8]}")
            if baseline:
                from quantview.funds.analytics import snapshot_difference

                difference = snapshot_difference(bundle, by_revision[baseline])
                st.metric("所选快照减基准 · 证券市值差额（元）", f"{difference['total_change']:+,.2f}")
                st.caption("逐项比较两份记录的市值；新增 / 不再列示仅表示快照范围变化。缺少成交、份额和资金流水，不能据此判断买卖、盈亏或收益率。")
                st.dataframe(pd.DataFrame([{"基金代码": row["code"], "基准市值（元）": float(row["baseline"]),
                                           "所选市值（元）": float(row["selected"]), "市值差额（元）": float(row["change"]),
                                           "列示变化": row["state"]} for row in difference["rows"]]), hide_index=True, width="stretch")
        except (ValueError, OSError) as exc:
            st.error(str(exc))
