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
quantview run-daily
quantview ui
```

未配置 AI Key 时，行情、指标和 Markdown 日报仍会正常生成，只跳过 AI 章节。

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
quantview init-db
quantview discover-contracts
quantview run-daily --date 2026-10-20
quantview backfill --start 2026-10-01 --end 2026-10-20
quantview import-file data/exports/manual_daily.csv
quantview ui
```

手工文件需包含 `contract_code`（或 `symbol`/`合约`/`合约代码`）以及日期、OHLC、成交量和持仓量字段；字段可使用系统标准英文名或 AKShare 常见中文名。

完整架构和数据口径见 [开发设计文档](docs/DEVELOPMENT_DESIGN.md)。

