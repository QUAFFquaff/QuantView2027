# QuantView2027

QuantView2027 是一个面向中国农产品期货的本地个人投研工作台。第一版覆盖大连商品交易所 LH（生猪）、JD（鸡蛋）和 C（玉米）的日行情、跨期价差、成交与持仓变化、AI 多空分析及 Markdown 日报。

系统仅用于研究，不包含实盘、模拟盘、账户连接或订单执行功能。

## 快速开始

要求 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
quantview init-db
quantview doctor
quantview status
quantview run-daily
quantview ui
```

未配置 AI Key 时，行情、指标和 Markdown 日报仍会正常生成，只跳过 AI 章节。

每日任务会核对目标日的已知合约覆盖：主源缺少有效当日行情时尝试备源；已知合约缺失或有效合约不足时标记 `partial`，跳过对应品种 AI，并在日报和工作台列出缺口与质量警告。最低合约数由 `config/app.yaml` 的 `data_quality.min_contracts_per_product` 配置，默认 2。完整性仅相对于本次发现或本地候选合约集合，不代表已核验交易所全量挂牌合约。

## 日常使用

打开工作台：

```powershell
.\scripts\start_ui.ps1
```

工作台侧栏会显示数据库最新日期，并提供“检查并补跑今天”按钮。安装工作日 18:10 自动任务：

```powershell
.\scripts\install_scheduled_task.ps1
```

安装定时任务会修改 Windows 任务计划，应由用户明确运行；项目不会自行注册系统任务。

## 常用命令

```powershell
quantview doctor
quantview status --date 2026-10-22
quantview init-db
quantview discover-contracts
quantview run-daily --date 2026-10-20
quantview backfill --start 2026-10-01 --end 2026-10-20
quantview import-file data/exports/manual_daily.csv
quantview ui
```

手工文件需包含 `contract_code`（或 `symbol`/`合约`/`合约代码`）以及日期、OHLC、成交量和持仓量字段；字段可使用系统标准英文名或 AKShare 常见中文名。

## 开发文档

- [开发设计与当前实现基线](docs/DEVELOPMENT_DESIGN.md)：架构、数据口径、现有能力与待完善项。
- [看板、邮件与复盘设计](docs/WORKBENCH_DESIGN.md)：页面、研究快照、纪律版本、决策记录、复盘及邮件契约。
- [实施与验收计划](docs/IMPLEMENTATION_PLAN.md)：P0–P4 任务、依赖和验收场景。
- [数据源说明](docs/DATA_SOURCES.md)：供应商字段与降级标记。

下一版方向是“本地看板为主、邮件为辅、决策与复盘留痕”，暂缓微信小程序。邮件、不可变研究快照、纪律版本和个人记录目前仍是设计目标，尚未实现；本文上面的使用命令对应现有功能。

