from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from quantview.funds.catalog import STARTERS, starter_watchlist
from quantview.funds.exposure import TEMPLATE, estimate, load_bundle, save_bundle
from quantview.metals.models import MetalsSettings
from quantview.metals.service import MetalsService
from quantview.operations.clock import shanghai_now


def _prices(service: MetalsService) -> None:
    st.subheader("场内价格")
    st.caption("与贵金属页共用自选和缓存。添加样例不会登记持仓；页面打开时只读取本地数据。")
    assets = service.watchlist()
    left, right = st.columns(2)
    if left.button("追加七只观察样例", key="funds_add_starters"):
        try:
            added = service.append_watchlist(starter_watchlist())
            st.success(f"已追加 {len(added)} 只，原有自选保留。" if added else "七只样例均已在自选中。")
            assets = service.watchlist()
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    if right.button("更新基金日线", key="funds_sync", type="primary", disabled=not assets):
        try:
            with st.status("正在更新基金日线…") as box:
                result = service.sync(funds_only=True, on_progress=lambda asset: st.write(f"更新 {asset.symbol}"))
                success = result["status"] == "success"
                box.update(label="更新完成" if success else "更新未全部成功，旧行情保留", state="complete" if success else "error")
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    if not assets:
        st.info("暂无基金自选。可追加样例，或在贵金属页保存自己的清单。")
        return

    rows = []
    for asset in assets:
        profile = STARTERS.get(asset.symbol) if asset.kind == "ETF" else None
        error = None
        try:
            snapshot = service.latest(asset)
            attempt = service.attempt(asset)
            if attempt and attempt["status"] == "failed":
                error = attempt["error"]
        except (ValueError, OSError, KeyError, TypeError) as exc:
            snapshot = None
            error = str(exc)
        quote = snapshot["quotes"][-1] if snapshot else {}
        day = quote.get("trade_date")
        stale = day and (shanghai_now().date() - pd.Timestamp(day).date()).days > service.settings.stale_after_calendar_days
        state = "行情陈旧" if stale else "已缓存" if snapshot else "待更新"
        if error:
            state = "更新或读取失败 · " + state
        rows.append({
            "代码": asset.symbol, "基金": profile.name if profile else snapshot["verification"]["name"] if snapshot else asset.name,
            "类别": asset.kind, "暴露类型": profile.exposure if profile else "未核验",
            "收盘价（元/份）": quote.get("close"), "行情日期": day,
            "状态": state, "行情来源": asset.source_url if snapshot else None,
            "产品资料": profile.source if profile else None,
        })
        if error:
            st.warning(f"{asset.symbol}：{error}")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        "行情来源": st.column_config.LinkColumn(display_text="查看"),
        "产品资料": st.column_config.LinkColumn(display_text="查看"),
        "收盘价（元/份）": st.column_config.NumberColumn(format="%.3f"),
    })
    st.caption("供应商日线未请求复权，非实时；当日数据在上海时间 18:10 前不展示。本页暂无单位净值和折溢价，不用旧净值拼接。")


def _exposure(settings: MetalsSettings) -> None:
    st.subheader("证券市值快照与基金穿透")
    st.caption("持仓金额来自同一时点的本地快照，不会用上方最新报价重算；成分权重来自当时已披露的基金报告。")
    with st.expander("导入本地快照与公开披露"):
        st.download_button("下载 JSON 模板", json.dumps(TEMPLATE, ensure_ascii=False, indent=2), "fund_exposure_template.json", "application/json")
        st.caption("可以只填部分基金；证券总市值须包含未填的证券，不含现金。导入会替换本页快照，文件保存在本地。")
        st.code('{"code":"560860","period_end":"2026-09-30","published_on":"2026-10-27","source_url":"https://…","holdings":[{"security_id":"SSE:600000","name":"示例股票","weight":0.10}]}', language="json")
        upload = st.file_uploader("选择快照 JSON", type="json", key="fund_exposure_file")
        if st.button("校验并保存快照", disabled=upload is None, key="fund_exposure_save"):
            try:
                save_bundle(settings.root, upload.getvalue())
                st.success("已保存到本地。")
            except (ValueError, OSError) as exc:
                st.error(str(exc))
    try:
        bundle = load_bundle(settings.root)
    except (ValueError, OSError) as exc:
        st.error(f"无法读取暴露快照：{exc}")
        return
    if bundle is None:
        st.info("尚无私有市值快照；上方行情仍可独立使用。")
        return
    view = estimate(bundle)
    total = view["total"]
    st.write(f"**估值时点：{view['as_of']:%Y-%m-%d %H:%M}（上海）**")
    st.text(f"快照来源：{bundle['source']}")
    columns = st.columns(3)
    columns[0].metric("快照证券总市值（元）", f"{total:,.2f}")
    columns[1].metric("已列基金占证券市值", f"{view['tracked'] / total:.1%}")
    columns[2].metric("已穿透成分占证券市值", f"{view['known'] / total:.1%}")
    groups = list(view["groups"].items())
    if view["outside"]:
        groups.append(("未纳入本视图", view["outside"]))
    st.dataframe(pd.DataFrame([
        {"产品暴露": name, "证券市值（元）": float(value), "占证券市值": f"{value / total:.2%}"}
        for name, value in sorted(groups, key=lambda row: -row[1])
    ]), hide_index=True, width="stretch")

    fund_rows = []
    for fund in view["funds"]:
        report = fund["report"] or {}
        fund_rows.append({
            "基金": f"{fund['name']} {fund['code']}", "产品暴露": fund["category"],
            "快照市值（元）": float(fund["value"]),
            "成分覆盖": f"{fund['known'] / fund['value']:.2%}" if report else None,
            "未穿透金额（元）": float(fund["remaining"]),
            "披露期末": report.get("period_end"), "发布日期": report.get("published_on"),
            "状态": fund["status"], "披露来源": report.get("source_url"),
        })
    if fund_rows:
        st.dataframe(pd.DataFrame(fund_rows), hide_index=True, width="stretch", column_config={
            "披露来源": st.column_config.LinkColumn(display_text="查看报告"),
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
    st.title("基金价格与暴露")
    st.caption("QuantView · 日线观察与可核验的部分穿透")
    try:
        _prices(MetalsService(settings))
    except (ValueError, OSError) as exc:
        st.error(f"基金行情不可用：{exc}")
    _exposure(settings)
