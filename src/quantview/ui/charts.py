from __future__ import annotations

import plotly.graph_objects as go


def style(figure, title, unit):
    figure.update_layout(
        title=title, height=330, margin=dict(l=12, r=12, t=50, b=20),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#334155"), hovermode="x unified",
        legend=dict(orientation="h", y=-0.2),
        xaxis_title=None, yaxis_title=unit,
    )
    figure.update_xaxes(showgrid=False)
    figure.update_yaxes(gridcolor="rgba(148,163,184,0.18)")
    return figure


def main_history(history, title, unit):
    figure = go.Figure()
    if not history.empty:
        history = history.sort_values("trade_date").copy()
        # Separate every contiguous stint, including A -> B -> A. Missing
        # values remain gaps within each stint.
        history["segment"] = history["main_contract"].ne(history["main_contract"].shift()).cumsum()
        for _, part in history.groupby("segment", sort=False):
            figure.add_trace(go.Scatter(
                x=part["trade_date"], y=part["main_settlement"],
                mode="lines+markers", name=str(part.iloc[0]["main_contract"]),
                connectgaps=False, line=dict(width=2), marker=dict(size=4),
            ))
    return style(figure, title, unit)


def contract_prices(bars, title, unit):
    figure = go.Figure()
    complete = bars[["open", "high", "low", "close"]].notna().all(axis=1)
    if complete.all():
        figure.add_trace(go.Candlestick(
            x=bars["trade_date"], open=bars["open"], high=bars["high"],
            low=bars["low"], close=bars["close"], name="真实 OHLC",
            increasing_line_color="#dc6262", decreasing_line_color="#269e87",
        ))
    else:
        figure.add_trace(go.Scatter(x=bars["trade_date"], y=bars["close"], name="收盘价（OHLC 不齐）"))
    figure.update_layout(xaxis_rangeslider_visible=False)
    return style(figure, title, unit)


def fixed_spread(history, title, unit):
    figure = go.Figure(go.Scatter(
        x=history["trade_date"], y=history["settlement_spread"], mode="lines+markers",
        connectgaps=False, name="近月 − 远月", line=dict(color="#248b86"),
    ))
    figure.add_hline(y=0, line_dash="dot", line_color="#94a3b8")
    return style(figure, title, unit)
