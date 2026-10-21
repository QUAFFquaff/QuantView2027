# 数据源说明

## 第一版接口

| 优先级 | Adapter | AKShare 接口 | 关键字段 |
|---|---|---|---|
| P0 | `akshare_sina` | `futures_zh_daily_sina` | date/open/high/low/close/volume/hold/settle |
| P1 | `akshare_em` | `futures_hist_em` | 时间/开盘/最高/最低/收盘/成交量/成交额/持仓量 |
| 合约发现 | `akshare_sina` | `futures_zh_realtime` | symbol/trade/position |

东方财富接口不提供结算价时，标准化层暂以收盘价填充，并写入 `settlement_filled_from_close` 质量标记。主源与备源均失败时，任务保留明确错误，不生成伪造数据。

交易所级 `get_futures_daily(market="DCE")` 当前不作为关键依赖，因为上游曾长期不可用。

## 数据治理

- 原始响应按来源、日期、运行批次保存为 Parquet。
- 业务层只读取标准 DuckDB 表，不直接依赖 AKShare 原始列名。
- 每条标准行情记录包含来源、抓取时间、批次和质量标记。
- 免费数据仅用于个人研究；公开或商业再分发前需重新核对上游条款。

