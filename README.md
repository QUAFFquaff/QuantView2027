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
# 先准备并核验包含每个自然日的 DCE 日历文件
quantview import-calendar data/exports/dce_calendar.csv --source "已核验的来源名称或网址" --version "来源版本" --verified
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

工作台侧栏显示应更新交易日、最近完整日、日历来源与覆盖、缺失日期。可明确点击“补跑最近缺失交易日”或选择历史补数区间；打开或刷新页面不会启动采集或 AI。历史补数默认关闭 AI。日历未知时按钮禁用，已有数据仍可浏览。

安装工作日 18:10 自动任务（Windows 必须使用中国标准时间；采集时间应与 `config/app.yaml` 一致）：

```powershell
.\scripts\install_scheduled_task.ps1
```

安装定时任务会修改 Windows 任务计划，应由用户明确运行；项目不会自行注册系统任务。

## 常用命令

```powershell
quantview doctor
quantview status --date 2026-10-22
quantview init-db
quantview import-calendar data/exports/dce_calendar.csv --source "已核验来源" --version "来源版本" --verified
quantview discover-contracts
quantview run-daily
quantview run-daily --date 2026-10-20
quantview catch-up
quantview catch-up --all
quantview backfill --start 2026-10-01 --end 2026-10-20
quantview import-file data/exports/manual_daily.csv
quantview ui
```

手工文件需包含 `contract_code`（或 `symbol`/`合约`/`合约代码`）以及日期、OHLC、成交量和持仓量字段；字段可使用系统标准英文名或 AKShare 常见中文名。

## 交易日历与日期规则

- `import-calendar` 的 CSV 仅含 `trade_date,is_trading_day` 两列。日期使用 `YYYY-MM-DD`，标记使用 `0/1` 或 `true/false`；须覆盖区间内每个自然日，包含周末和节假日。重复、缺口和非法值拒绝导入；覆盖修订在事务中完成，旧导入的来源、版本、时间与文件哈希保留。
- `--verified` 是调用者确认已核验文件，应用不会自动认证任意文件为官方日历。项目没有内置或自动下载真实日历，测试样本不能用作运行日历。请核对实际 DCE 来源，并按年份维护覆盖。
- 不指定 `run-daily --date` 时，按上海当前时间及 `pipeline.scheduled_time`（默认 18:10）选最近已到采集时间的交易日。休市日或收盘前可选前一交易日，未知日历或覆盖缺口不回退到工作日推算。
- 明确指定的未来日期或尚未到采集时间的当日被阻止；已核验非交易日返回 `skipped`，不采集。过去日期若未被日历覆盖，仅明确日期的手动运行可继续，记录警告、标记 `partial` 并跳过 AI。调度与受控补跑不能绕过日历检查。
- `backfill` 先核验整个区间，再执行其中的交易日；默认不调用 AI，需明确 `--with-ai` 才启用。`catch-up` 默认仅补最近一个缺失日，`--all` 明确补所显示缺口。空库默认只提示最近应更新的一日。
- `status` 默认按上海当前时刻判断，`status --date` 按指定日的采集截止时间评估。连续计数从应更新日向前，遇到未成功或未知日期即中断；历史回填和事后生成不计入。该计数尚不代替真实行情、快照及自动运行的完整验收。

## 开发文档

- [开发设计与当前实现基线](docs/DEVELOPMENT_DESIGN.md)：架构、数据口径、现有能力与待完善项。
- [看板、邮件与复盘设计](docs/WORKBENCH_DESIGN.md)：页面、研究快照、纪律版本、决策记录、复盘及邮件契约。
- [实施与验收计划](docs/IMPLEMENTATION_PLAN.md)：P0–P4 任务、依赖和验收场景。
- [数据源说明](docs/DATA_SOURCES.md)：供应商字段与降级标记。
- [10 月 26 日进度与验收](docs/PROGRESS_2026-10-26.md)：日历、选日、补跑及本轮边界。

下一版方向是“本地看板为主、邮件为辅、决策与复盘留痕”，暂缓微信小程序。邮件、不可变研究快照、纪律版本和个人记录目前仍是设计目标，尚未实现；本文上面的使用命令对应现有功能。

