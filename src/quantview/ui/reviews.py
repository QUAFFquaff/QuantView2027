from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st


def render_reviews(root: Path) -> None:
    st.title("行情复盘")
    st.caption("查看已保存的行情分析与交易复盘，按报告日期保留当时的判断。")
    reports = {}
    for path in (root / "data/exports/private-review").glob("market-review-????-??-??.md"):
        try:
            report_date = date.fromisoformat(path.stem.removeprefix("market-review-"))
        except ValueError:
            continue
        reports[report_date] = path
    if not reports:
        st.info("暂无已保存的行情复盘。")
        return
    selected = st.selectbox("复盘日期", sorted(reports, reverse=True), key="review_date")
    try:
        report = reports[selected].read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        st.error("这份复盘暂时无法读取，请刷新后重试。")
        return
    st.caption(f"最新复盘 {max(reports)} · 当前查看 {selected}。报告观点对应其注明的行情时点。")
    st.download_button("下载复盘 Markdown", report, reports[selected].name, "text/markdown")
    st.markdown(report)
