# QuantView2027 开发设计文档

> 面向中国农产品期货的个人投研工作台  
> 文档状态：Draft v0.1  
> 更新日期：2026-10-20  
> 首期范围：大连商品交易所 LH（生猪）、JD（鸡蛋）、C（玉米）  
> 系统边界：仅用于研究，不接入实盘、模拟盘或任何下单接口

## 1. 文档目的

本文档定义 QuantView2027 第一版的产品范围、系统架构、数据口径、模块职责、运行流程、AI 分析约束、测试策略和实施计划，作为后续编码、验收与迭代的共同基线。

第一版不追求构建完整量化交易系统，而是先打通一条稳定、可验证、可追溯的日频投研链路：

> 日行情采集 → 数据校验与标准化 → 主力及期限结构计算 → 成交持仓分析 → AI 多空研究 → Markdown 日报 → 本地可视化查看

## 2. 核心决策

### 2.1 部署和运行形态

采用 **Local-first Python 应用**：

- Python 负责数据采集、计算、AI 调用、报告生成和本地 Web UI。
- DuckDB 保存标准化数据，Parquet 保存原始数据快照。
- Streamlit 提供本地可视化工作台。
- Windows 任务计划程序在交易日收盘后运行一次日任务。
- 工作台启动时检查缺失交易日，并允许补跑。
- GitHub 仅用于源代码版本管理、测试和备份，不作为第一版运行后台。
- 系统无需 24 小时常驻；日报生成后，应用和电脑均可关闭。

推荐运行时间为交易日 18:10。若电脑届时关闭，则下次打开工作台时补跑缺失数据。

### 2.2 技术选型

| 层次 | 第一版选型 | 说明 |
|---|---|---|
| 语言 | Python 3.11/3.12 | 满足 AKShare 当前版本要求 |
| 包与环境管理 | `uv` + `pyproject.toml` | 锁定依赖并提高环境可复现性 |
| 数据处理 | Pandas | 三个品种的日频数据规模较小 |
| 标准数据仓库 | DuckDB | 单文件、适合时序与横截面分析，无需数据库服务 |
| 原始数据 | Parquet | 保存上游原始快照，支持追溯和重算 |
| 数据源 | AKShare adapter | 首期免费数据源，必须隔离在适配层之后 |
| 配置 | YAML + `.env` | 业务参数与密钥分离 |
| AI 接入 | 轻量 provider adapter | 支持 OpenAI-compatible API，首期不引入完整 LangGraph |
| 报告 | Jinja2 → Markdown | 可读、可归档、可追溯 |
| UI | Streamlit + Plotly | 快速完成个人本地工作台 |
| 测试 | Pytest | 覆盖数据口径、换月、价差和异常处理 |
| 调度 | Windows Task Scheduler | 每日执行后退出，不运行常驻后台 |

### 2.3 上游项目的复用原则

| 项目 | 复用内容 | 不直接复用内容 |
|---|---|---|
| ZhuLinsen/daily_stock_analysis | 每日任务闭环、数据源降级、运行状态、报告模板、历史报告浏览 | 股票基本面、股票代码解析、持仓管理、推送矩阵及完整依赖体系 |
| TauricResearch/TradingAgents | Bull/Bear/Judge 分工、结构化状态、证据引用、失败检查点思想 | 完整 LangGraph、Trader、Portfolio Manager、订单与交易执行语义 |
| akfamily/akshare | 期货行情、合约和持仓类数据接口 | 业务层直接依赖其字段；将其视为稳定数据库 |

如复制上游源代码，应保留许可证和版权声明，并维护 `THIRD_PARTY_NOTICES.md`。仅借鉴设计思想时，仍应在项目文档中记录参考来源。

## 3. 产品范围

### 3.1 MVP 纳入范围

1. LH、JD、C 全部有效挂牌合约的日行情。
2. OHLC、结算价、成交量和持仓量。
3. 主力、次主力识别及主力切换记录。
4. 同品种合约期限结构和跨期价差。
5. 成交量、持仓量的日变化、占比和滚动异常度。
6. 价格—成交—持仓的确定性状态描述。
7. Bull、Bear、Judge 三角色 AI 分析。
8. Markdown 日报、历史日报和本地可视化工作台。
9. 数据来源、数据新鲜度、质量标记和运行状态展示。

### 3.2 明确排除范围

- 实盘或模拟盘下单。
- 期货公司、券商或交易终端账户连接。
- 自动仓位、订单、止损止盈或执行算法。
- 分钟级行情、盘口、实时行情推送和盘中告警。
- 回测、组合收益归因和资金管理。
- 期权、仓单、库存、现货基差、养殖利润、天气和新闻。
- 会员多空持仓排名。
- 多用户、权限系统和公网部署。

这些能力只能在 MVP 稳定后按独立模块引入，不得提前污染第一版核心数据模型。

## 4. 架构总览

```text
AKShare / 手工 CSV
        │
        ▼
Data Source Adapters
        │  原始响应与抓取元数据
        ▼
Raw Snapshot Store (Parquet)
        │
        ▼
Normalizer + Validator
        │  标准代码、字段、类型、质量标记
        ▼
DuckDB Canonical Store
        │
        ├──────────────┐
        ▼              ▼
Indicator Engine   Run/Audit Store
        │
        ├─ 主力/次主力
        ├─ 期限结构/价差
        ├─ 成交与持仓变化
        └─ 趋势/波动/异常度
        │
        ▼
Evidence Pack Builder
        │
        ▼
Bull → Bear → Judge
        │  结构化 JSON
        ▼
Report Renderer (Markdown)
        │
        ├─ 历史日报目录
        └─ Streamlit 本地工作台
```

### 4.1 分层原则

1. **数据源隔离**：除 adapter 外，任何模块不得直接 import 或调用 AKShare。
2. **原始数据不可变**：成功抓取的原始响应以批次保存，不覆盖历史快照。
3. **先计算、后解释**：收益率、价差、Z-score 等由确定性代码计算，LLM 不负责算数。
4. **AI 可降级**：AI 失败不能阻止行情与确定性日报生成。
5. **可重跑**：相同交易日重复运行必须幂等；修复数据后可按日期重算。
6. **可追溯**：每份报告均能定位数据源、抓取批次、代码版本、提示词版本和模型。
7. **只读研究边界**：工程中不创建 `broker`、`order`、`execution` 等交易执行模块。

## 5. 建议目录结构

```text
QuantView2027/
├─ pyproject.toml
├─ README.md
├─ .env.example
├─ config/
│  ├─ app.yaml
│  ├─ products.yaml
│  ├─ spreads.yaml
│  └─ prompts/
│     ├─ bull_v1.md
│     ├─ bear_v1.md
│     └─ judge_v1.md
├─ docs/
│  ├─ DEVELOPMENT_DESIGN.md
│  ├─ DATA_SOURCES.md
│  └─ THIRD_PARTY_NOTICES.md
├─ src/quantview/
│  ├─ cli.py
│  ├─ config.py
│  ├─ domain/
│  │  ├─ models.py
│  │  ├─ enums.py
│  │  └─ rules.py
│  ├─ data/
│  │  ├─ adapters/
│  │  │  ├─ base.py
│  │  │  ├─ akshare_sina.py
│  │  │  ├─ akshare_em.py
│  │  │  └─ csv_import.py
│  │  ├─ normalization.py
│  │  ├─ validation.py
│  │  ├─ raw_store.py
│  │  └─ repository.py
│  ├─ analytics/
│  │  ├─ dominant_contract.py
│  │  ├─ term_structure.py
│  │  ├─ spreads.py
│  │  ├─ volume_oi.py
│  │  └─ indicators.py
│  ├─ ai/
│  │  ├─ providers.py
│  │  ├─ evidence.py
│  │  ├─ schemas.py
│  │  └─ workflow.py
│  ├─ reports/
│  │  ├─ renderer.py
│  │  └─ templates/
│  │     └─ daily_report.md.j2
│  ├─ pipeline/
│  │  ├─ daily.py
│  │  ├─ backfill.py
│  │  └─ stages.py
│  └─ ui/
│     ├─ app.py
│     └─ pages/
├─ data/
│  ├─ raw/
│  ├─ warehouse/
│  │  └─ quantview.duckdb
│  └─ exports/
├─ reports/
│  └─ daily/
├─ logs/
└─ tests/
   ├─ unit/
   ├─ integration/
   ├─ fixtures/
   └─ regression/
```

运行时目录 `data/`、`reports/`、`logs/` 默认不提交行情数据和密钥到 Git。可提交脱敏的小型测试 fixtures。

## 6. 数据源设计

### 6.1 数据源优先级

| 优先级 | 数据源 | 用途 | 降级策略 |
|---|---|---|---|
| P0 | AKShare `futures_zh_daily_sina` | 单个真实合约历史日线 | 失败后转 P1 |
| P1 | AKShare `futures_hist_em` | 单合约日线回退 | 字段标准化后校验 |
| P2 | 手工 CSV/Excel 导入 | 免费接口全部失效时兜底 | 明确标记 `manual` |
| 辅助 | `futures_zh_spot` | 当前合约发现 | 失败时使用本地注册表 |
| 辅助 | `futures_main_sina` | 连续趋势参考 | 不参与真实合约价差 |

交易所级 `get_futures_daily(..., market="DCE")` 不作为首期关键依赖。其 DCE 接口存在长期不可用记录，只有在数据可行性验证通过后才能作为额外来源。

### 6.2 Adapter 契约

每个行情 adapter 对外返回统一记录，不暴露供应商字段：

```text
DailyBarRaw
  source
  source_symbol
  exchange
  product
  contract_code
  trade_date
  open
  high
  low
  close
  settlement
  pre_settlement?
  volume
  open_interest
  turnover?
  fetched_at
  raw_batch_id
```

Adapter 只负责获取和最低限度解析；类型转换、合约标准化、业务校验在 normalization/validation 层完成。

### 6.3 合约发现

合约集合来自三部分的并集：

1. 当前行情接口发现的 LH/JD/C 合约；
2. 本地 `instrument` 注册表中仍可能有效的合约；
3. `products.yaml` 中人工补充的观察合约。

系统不得只抓主力连续合约，否则无法可靠计算期限结构、价差及持仓迁移。

### 6.4 原始快照

建议路径：

```text
data/raw/{source}/{trade_date}/{run_id}/{endpoint}_{symbol}.parquet
```

同时保存：请求参数、AKShare 版本、抓取时间、响应行数和内容哈希。原始快照用于复核供应商字段变化和离线重算。

## 7. 数据模型与数据字典

### 7.1 `instrument`

主键：`instrument_id`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `instrument_id` | VARCHAR | 是 | 标准标识，如 `DCE.LH.2601` |
| `exchange` | VARCHAR | 是 | 首期固定为 `DCE` |
| `product` | VARCHAR | 是 | `LH`、`JD`、`C` |
| `contract_code` | VARCHAR | 是 | 标准合约代码，如 `LH2601` |
| `delivery_year` | INTEGER | 是 | 交割年 |
| `delivery_month` | INTEGER | 是 | 交割月 |
| `listed_date` | DATE | 否 | 上市日 |
| `last_trade_date` | DATE | 否 | 最后交易日 |
| `contract_multiplier` | DOUBLE | 否 | 合约乘数 |
| `price_tick` | DOUBLE | 否 | 最小变动价位 |
| `is_active` | BOOLEAN | 是 | 当前是否纳入采集 |
| `source_symbol` | VARCHAR | 是 | 上游接口代码 |
| `updated_at` | TIMESTAMP | 是 | 元数据更新时间 |

### 7.2 `futures_daily`

主键：`trade_date + instrument_id`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `trade_date` | DATE | 是 | 交易日 |
| `instrument_id` | VARCHAR | 是 | 标准合约标识 |
| `open` | DOUBLE | 是 | 开盘价 |
| `high` | DOUBLE | 是 | 最高价 |
| `low` | DOUBLE | 是 | 最低价 |
| `close` | DOUBLE | 是 | 收盘价 |
| `settlement` | DOUBLE | 是 | 当日结算价 |
| `pre_settlement` | DOUBLE | 否 | 前结算价 |
| `volume` | BIGINT | 是 | 成交量，单位手 |
| `open_interest` | BIGINT | 是 | 持仓量，单位手 |
| `turnover` | DOUBLE | 否 | 成交额 |
| `close_change` | DOUBLE | 否 | 收盘价日变化 |
| `close_return` | DOUBLE | 否 | 收盘价收益率 |
| `settlement_return` | DOUBLE | 否 | 结算价收益率 |
| `volume_change` | BIGINT | 否 | 成交量日变化 |
| `volume_change_pct` | DOUBLE | 否 | 成交量变化率 |
| `oi_change` | BIGINT | 否 | 持仓量日变化 |
| `oi_change_pct` | DOUBLE | 否 | 持仓量变化率 |
| `source` | VARCHAR | 是 | 标准来源名 |
| `source_fetched_at` | TIMESTAMP | 是 | 抓取时间 |
| `raw_batch_id` | VARCHAR | 是 | 原始数据批次 |
| `quality_status` | VARCHAR | 是 | `ok/warning/rejected` |
| `quality_flags` | JSON | 是 | 质量问题列表 |

变化率前值为零或缺失时保存 `NULL`，不得用 0 替代未知结果。

### 7.3 `product_daily`

主键：`trade_date + product`

| 字段 | 类型 | 说明 |
|---|---|---|
| `trade_date` | DATE | 交易日 |
| `product` | VARCHAR | LH/JD/C |
| `main_contract` | VARCHAR | 主力合约 |
| `second_contract` | VARCHAR | 次主力合约 |
| `main_changed` | BOOLEAN | 是否发生主力切换 |
| `previous_main_contract` | VARCHAR | 前一交易日主力 |
| `selection_method` | VARCHAR | `open_interest` 或回退方法 |
| `total_volume` | BIGINT | 全部有效合约成交量之和 |
| `total_open_interest` | BIGINT | 全部有效合约持仓量之和 |
| `total_volume_change` | BIGINT | 品种成交量变化 |
| `total_oi_change` | BIGINT | 品种持仓变化 |
| `main_volume_share` | DOUBLE | 主力成交占比 |
| `main_oi_share` | DOUBLE | 主力持仓占比 |
| `price_oi_regime` | VARCHAR | 量价仓状态 |
| `return_5d` | DOUBLE | 5 日收益率 |
| `return_20d` | DOUBLE | 20 日收益率 |
| `atr_20` | DOUBLE | 20 日 ATR |
| `volume_zscore_20` | DOUBLE | 成交量异常度 |
| `oi_change_zscore_20` | DOUBLE | 持仓变化异常度 |

### 7.4 `calendar_spread_daily`

主键：`trade_date + near_instrument_id + far_instrument_id`

| 字段 | 类型 | 说明 |
|---|---|---|
| `trade_date` | DATE | 交易日 |
| `product` | VARCHAR | LH/JD/C |
| `near_instrument_id` | VARCHAR | 近月合约 |
| `far_instrument_id` | VARCHAR | 远月合约 |
| `near_delivery_month` | DATE | 近月交割月份 |
| `far_delivery_month` | DATE | 远月交割月份 |
| `close_spread` | DOUBLE | 近月收盘－远月收盘 |
| `settlement_spread` | DOUBLE | 近月结算－远月结算 |
| `spread_pct` | DOUBLE | 价差占近月价格比例 |
| `spread_change` | DOUBLE | 价差日变化 |
| `zscore_20` | DOUBLE | 20 日标准分 |
| `zscore_60` | DOUBLE | 60 日标准分 |
| `percentile_60` | DOUBLE | 60 日分位数 |
| `near_volume` | BIGINT | 近月成交量 |
| `far_volume` | BIGINT | 远月成交量 |
| `near_oi` | BIGINT | 近月持仓量 |
| `far_oi` | BIGINT | 远月持仓量 |
| `liquidity_status` | VARCHAR | 流动性状态 |
| `pair_type` | VARCHAR | `main_second/adjacent/configured` |

### 7.5 `ai_analysis`

| 字段 | 类型 | 说明 |
|---|---|---|
| `analysis_id` | VARCHAR | 分析 ID |
| `trade_date` | DATE | 目标交易日 |
| `product` | VARCHAR | LH/JD/C 或 MARKET |
| `bull_case` | TEXT | 多头论据 |
| `bear_case` | TEXT | 空头论据 |
| `judge_view` | TEXT | 裁决与综合判断 |
| `bias` | VARCHAR | `bullish/neutral/bearish/insufficient_data` |
| `confidence` | DOUBLE | 0–1 模型自评，不等于统计置信度 |
| `key_evidence` | JSON | 使用的指标和值 |
| `risk_flags` | JSON | 风险和反例 |
| `invalidation_conditions` | JSON | 推翻当前判断的条件 |
| `provider` | VARCHAR | 模型服务商 |
| `model` | VARCHAR | 模型名称 |
| `prompt_version` | VARCHAR | 提示词版本 |
| `input_hash` | VARCHAR | Evidence Pack 哈希 |
| `created_at` | TIMESTAMP | 生成时间 |

### 7.6 `pipeline_run`

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | VARCHAR | 运行批次 |
| `target_trade_date` | DATE | 目标交易日 |
| `trigger_type` | VARCHAR | `scheduled/manual/catch_up/rebuild` |
| `started_at` | TIMESTAMP | 开始时间 |
| `finished_at` | TIMESTAMP | 结束时间 |
| `status` | VARCHAR | `running/success/partial/failed` |
| `stages` | JSON | 每阶段状态与耗时 |
| `rows_fetched` | INTEGER | 抓取行数 |
| `rows_accepted` | INTEGER | 入库行数 |
| `rows_rejected` | INTEGER | 拒绝行数 |
| `source_errors` | JSON | 上游错误 |
| `report_path` | VARCHAR | 日报路径 |
| `code_version` | VARCHAR | 代码版本 |

## 8. 核心计算规则

### 8.1 主力与次主力

默认规则：

1. 过滤非真实合约、已到期合约和无效数据。
2. 以当日 `open_interest` 降序选主力和次主力。
3. 持仓缺失时才允许以成交量回退，并设置质量警告。
4. 主力相同持仓时，优先成交量更高的合约；仍相同时选择近月。
5. 保存每日选取结果，不使用今天的结果回写历史。
6. 主力切换日同时展示旧主力和新主力，避免把换月跳空解释为单合约收益。

连续趋势展示与真实合约分析分离。供应商连续合约不得用于跨期价差。

### 8.2 合约价差

首期只支持同品种跨期价差：

```text
settlement_spread = near.settlement - far.settlement
close_spread      = near.close - far.close
spread_pct        = settlement_spread / near.settlement
```

默认以结算价价差作为研究主口径。合约对包括：

- 主力－次主力；
- 按交割月份相邻且满足流动性条件的合约；
- 配置文件指定的固定观察合约对。

不默认计算年化展期收益率。若后续加入，必须根据真实交割月份间隔计算，并明确年化假设。

### 8.3 流动性过滤

阈值放入配置，不能硬编码。初始设计支持：

- 最低当日成交量；
- 最低持仓量；
- 是否允许零成交但仍有持仓的合约；
- 距最后交易日的最小天数；
- 近远月两腿是否均满足条件。

最终阈值在数据可行性阶段根据 LH、JD、C 的实际分布确定。

### 8.4 成交与持仓变化

同时计算单合约和品种汇总口径：

```text
volume_change     = volume[t] - volume[t-1]
oi_change         = open_interest[t] - open_interest[t-1]
main_volume_share = main_volume / product_total_volume
main_oi_share     = main_oi / product_total_open_interest
```

主力切换时，主力序列的变化不得直接使用新旧两个合约相减。单合约变化按合约自身历史计算；品种变化按全部有效合约汇总后计算。

### 8.5 量价仓状态

第一版只给出观察性分类：

| 价格变化 | 持仓变化 | 状态代码 | 表述 |
|---|---|---|---|
| 正 | 正 | `up_add` | 上涨伴随增仓 |
| 正 | 负 | `up_reduce` | 上涨伴随减仓 |
| 负 | 正 | `down_add` | 下跌伴随增仓 |
| 负 | 负 | `down_reduce` | 下跌伴随减仓 |
| 接近零 | 任意 | `flat` | 价格变化不显著 |

总持仓不能识别多头和空头分别是谁，系统及 AI 均不得仅凭该四象限断言“多头主动增仓”或“空头主动增仓”。

## 9. 数据质量规则

### 9.1 阻断性错误 `rejected`

- 合约代码无法解析。
- 交易日无效。
- OHLC 明显违反 `low <= open/close <= high`。
- 价格、成交量或持仓量为非法负数。
- 同一来源同一主键出现内容冲突且无法确定正确记录。

### 9.2 警告性错误 `warning`

- 结算价缺失但收盘价存在。
- 成交量为零但仍有持仓。
- 单日价格、成交量或持仓变化超出配置阈值。
- 主源与备源在同字段上的差异超过容忍度。
- 活跃合约数量明显少于近期水平。
- 目标交易日数据尚未全部更新。

### 9.3 数据完整性门槛

日报状态分三类：

- `complete`：三品种满足最低完整性并完成全部指标。
- `partial`：确定性数据部分缺失，但报告明确标记缺口。
- `failed`：关键日行情不可用，不生成市场结论，仅生成失败报告。

AI 分析只在对应品种的 Evidence Pack 达到最低完整性门槛时运行。

## 10. AI 分析设计

### 10.1 工作流

```text
Evidence Pack
    ├─ Bull Analyst：构造最强看多论证
    ├─ Bear Analyst：构造最强看空论证及反例
    └─ Judge：比较证据强弱，给出条件化结论
```

为控制成本，Bull 与 Bear 可以并行，Judge 读取两者结果。第一版不使用递归辩论，也不赋予 agent 自主调用外部工具的能力。

### 10.2 Evidence Pack

每个品种的输入至少包含：

- 数据日期、来源与完整性；
- 主力、次主力及是否换月；
- 主力价格变化、5/20 日收益、ATR；
- 品种成交与持仓变化；
- 主力成交和持仓占比；
- 关键价差、历史 Z-score 和分位数；
- 异常指标和质量警告；
- 可引用的 evidence ID。

不在 Evidence Pack 中的信息，模型不得作为事实写入结论。

### 10.3 结构化输出

模型输出必须通过 schema 校验，至少包含：

```json
{
  "bias": "bullish | neutral | bearish | insufficient_data",
  "confidence": 0.0,
  "thesis": "...",
  "evidence_ids": ["..."],
  "counterarguments": ["..."],
  "risk_flags": ["..."],
  "invalidation_conditions": ["..."]
}
```

`confidence` 只是模型对证据一致性的自评，不得描述为统计显著性或胜率。

### 10.4 防幻觉与降级

- 提示词明确禁止补充未提供的供需、政策、天气、现货和新闻事实。
- Evidence 引用必须存在，否则输出无效并重试一次。
- JSON 校验失败时进行一次修复；仍失败则标记 AI 阶段失败。
- 单个品种 AI 失败不影响其他品种和确定性报告。
- 未配置 API Key 时，系统生成无 AI 章节的完整数据日报。
- 报告不得出现具体手数、开仓/平仓指令或自动执行建议。

## 11. 日任务设计

### 11.1 正常运行时序

```text
18:10 触发任务
  → 判断目标日期是否为交易日
  → 创建 pipeline_run
  → 更新合约注册表
  → 抓取 LH/JD/C 真实合约日行情
  → 保存原始快照
  → 标准化、校验、幂等入库
  → 检查当日完整性
  → 计算主力、期限结构、价差和量仓指标
  → 构造 Evidence Pack
  → 运行 Bull/Bear/Judge
  → 生成 Markdown 日报
  → 更新 pipeline_run 并退出
```

### 11.2 启动补跑

打开工作台时：

1. 读取数据库最新成功交易日；
2. 根据交易日历计算缺失日期；
3. 显示缺失范围和预计任务数；
4. 默认仅自动补最近一个缺失交易日；
5. 多日历史补数由用户明确触发，避免打开页面后长时间阻塞；
6. 补数完成后刷新工作台。

### 11.3 幂等与并发

- 同一交易日允许重复抓取，但标准表按主键 upsert。
- 原始快照按 run ID 新增，不覆盖。
- 同一目标日期只允许一个活跃 pipeline run。
- 使用文件锁或 DuckDB 锁阻止调度任务与手动任务并发写入。
- 报告先写临时文件，完成后原子替换正式文件。

## 12. Markdown 日报设计

日报路径：

```text
reports/daily/{YYYY}/{MM}/{YYYY-MM-DD}.md
```

固定结构：

1. 报告元数据：交易日、生成时间、代码版本、数据来源、完整性。
2. 市场总览：LH/JD/C 主力、涨跌、成交持仓变化、关键价差和 AI 偏向。
3. LH 分析。
4. JD 分析。
5. C 分析。
6. 期限结构和跨期价差。
7. 成交量、持仓量和主力迁移。
8. Bull/Bear/Judge 观点及推翻条件。
9. 数据质量与缺失项。
10. 风险声明：仅用于个人研究，不构成投资建议，无下单功能。

日报必须保留模型和数据的不确定性，不得用单一“看多/看空”标签替代证据和反例。

## 13. 可视化工作台设计

### 13.1 今日总览

- 最近完整交易日和数据新鲜度。
- 三品种主力合约、结算涨跌、成交/持仓变化。
- 主力切换提示。
- 关键跨期价差及 60 日分位。
- AI 偏向、置信度和数据完整性。

### 13.2 品种研究

- 主力真实合约 K 线。
- 成交量、持仓量及其变化。
- 5/20 日收益、ATR 和异常度。
- 主力/次主力及历史换月。
- Bull、Bear、Judge 完整分析。

### 13.3 期限结构

- 当前全部有效合约结算价曲线。
- 前一日或指定历史日对比曲线。
- 主力－次主力、相邻月和固定合约对价差。
- 价差历史、Z-score、分位数和两腿流动性。

### 13.4 日报与系统状态

- 按日期浏览 Markdown 日报。
- 数据源成功率和最近错误。
- pipeline stage 状态和耗时。
- 缺失日期、质量警告和手动补跑入口。

第一版 UI 只读。补跑按钮调用受控的 pipeline 命令，不允许页面直接执行任意代码或修改标准数据。

## 14. 配置设计

`app.yaml`：

- 数据目录和报告目录；
- 交易日任务时间；
- 默认回看窗口；
- 数据源顺序与重试；
- AI 是否启用、模型名、超时和最大预算；
- 日志级别。

`products.yaml`：

- LH/JD/C 名称、交易所、允许交割月份；
- 合约乘数、最小变动价位；
- 流动性阈值；
- 价格异常阈值。

`spreads.yaml`：

- 默认主力－次主力；
- 是否生成全部相邻月价差；
- 固定观察合约对；
- Z-score 窗口；
- 到期过滤规则。

`.env` 只保存密钥和服务地址，不得提交 Git。

## 15. 日志、审计与安全

- 使用结构化日志，包含 `run_id`、`trade_date`、`stage`、`source`。
- 日志不得输出 API Key、完整请求头或含密钥的 URL。
- 每次 AI 调用记录 provider、model、耗时、token 用量和输入哈希。
- 原始 Prompt 可以落盘，但必须确保其中不含密钥。
- Git 忽略 `.env`、DuckDB、原始行情、生成报告和运行日志。
- Streamlit 默认只监听 `127.0.0.1`，不直接暴露到公网。
- 项目中禁止引入交易账户凭证和订单执行依赖。

## 16. 测试策略

### 16.1 单元测试

- DCE 合约代码解析及跨年处理。
- OHLC、成交量、持仓量校验。
- 主力/次主力选择及相同持仓时的 tie-break。
- 主力切换日处理。
- 近远月排序和价差方向。
- 变化率零分母。
- Z-score 样本不足和零方差。
- 量价仓四象限。
- AI schema 与 evidence 引用校验。

### 16.2 集成测试

- 每个数据源 adapter 的小规模真实调用。
- 主源失败后转备源。
- 原始快照到 DuckDB 的完整链路。
- 同一日期重复执行的幂等性。
- AI 未配置、超时和非法 JSON 的降级。
- Markdown 报告生成。

真实网络测试必须标记为 integration，默认测试不调用外部网络。

### 16.3 回归测试

固定若干典型日期：

- 普通交易日；
- 主力切换日；
- 某合约零成交日；
- 年份切换附近；
- 上游字段异常或缺失日。

保存脱敏、小型 fixtures 和预期指标结果。代码变更后比较数据行数、主力结果、价差和报告关键字段。

### 16.4 MVP 验收标准

1. 连续 5 个交易日自动生成三品种日报。
2. 任一单一数据源失败时，系统能降级或明确生成失败状态，不产生无标记的错误数据。
3. 相同交易日重跑不产生重复标准记录。
4. 主力切换不会造成伪造的单日收益跳变。
5. 所有 AI 数值均可追溯到 Evidence Pack。
6. AI 服务不可用时仍能生成确定性数据报告。
7. 工作台关闭后无后台进程；代码中无下单接口。

## 17. 实施计划

### 阶段 0：数据可行性验证，1–2 天

- 验证 LH/JD/C 活跃合约发现。
- 验证最近 60 个交易日的价格、结算、成交和持仓完整性。
- 验证主源、备源的字段和差异。
- 固化回归测试样本。

退出条件：能够构建同一交易日的完整合约横截面。若失败，优先解决数据源，不进入 UI 开发。

### 阶段 1：数据底座，3–4 天

- 建立项目骨架、配置和 DuckDB schema。
- 实现 adapter、原始快照、标准化、校验和增量入库。
- 建立 CSV 兜底导入。
- 完成数据层测试。

### 阶段 2：分析指标，3–4 天

- 实现主力/次主力及换月。
- 实现期限结构和价差。
- 实现成交持仓变化、趋势和异常指标。
- 完成边界条件测试。

### 阶段 3：AI 与日报，2–3 天

- 实现 Evidence Pack 和结构化 schema。
- 实现 Bull/Bear/Judge。
- 实现校验、重试和无 AI 降级。
- 生成 Markdown 日报。

### 阶段 4：工作台，3–5 天

- 今日总览、品种研究、期限结构、日报与状态页面。
- 加入缺失数据提示和受控补跑。
- 完成关键图表和交互验收。

### 阶段 5：调度与试运行，2 天 + 5 个交易日观察

- 配置 Windows 任务计划。
- 实现启动补跑和运行锁。
- 连续观察至少 5 个交易日。
- 修复数据更新时间、上游波动和换月问题。

## 18. 主要风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| DCE 免费接口变更或失效 | 当日行情缺失 | Adapter 隔离、双源回退、原始缓存、CSV 兜底 |
| 供应商连续合约口径不透明 | 趋势收益失真 | 核心分析使用真实合约，自行记录主力切换 |
| 合约代码跨年解析错误 | 合约排序与价差错误 | 明确标准标识，使用回归 fixtures 测试 |
| 主力切换造成伪涨跌 | 日报结论错误 | 单合约收益与连续展示分离，换月日显式提示 |
| LLM 编造基本面或多空行为 | 形成虚假确定性 | 仅允许引用 Evidence Pack，schema 和 evidence 校验 |
| 电脑在计划时间关闭 | 当日报告未生成 | 启动补跑；后续再考虑 NAS/VPS/GitHub Actions |
| Streamlit 后期扩展受限 | 复杂交互困难 | 保持 analytics/repository 与 UI 解耦，后续可迁移 FastAPI + React |

## 19. 后续路线但不属于 MVP

建议迭代顺序：

1. 现货价格、基差、养殖利润和饲料成本。
2. 仓单、库存和 DCE 会员持仓排名。
3. 农业政策、天气、疫病和产业新闻证据库。
4. 日报 HTML/PDF、邮件或企业微信推送。
5. Docker 化并部署到个人 NAS/VPS，通过私有网络远程访问。
6. 回测与历史观点复盘。
7. 在数据和提示词稳定后，评估是否引入 LangGraph 和研究记忆。

即使进入后续阶段，实盘下单仍不属于本项目默认路线；若未来改变该边界，必须单独立项、单独设计安全与权限模型。

## 20. 待确认项

以下参数在阶段 0 用真实数据确定，不阻塞当前架构：

1. LH/JD/C 各自的最低成交量和持仓量门槛。
2. 默认固定观察的跨期合约对。
3. 历史初始化窗口：建议先取 1–3 年，而不是一次抓取全部历史。
4. AI 服务商、模型和每日调用预算。
5. 报告是否需要在 MVP 后增加 HTML/PDF 导出。

除上述可配置项外，Local-first、按日运行、真实合约为核心、AI 只解释结构化证据、禁止交易执行，是第一版不可变的架构约束。
