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

- 农产品原始响应按来源、日期、运行批次保存为 Parquet；贵金属公开缓存使用下文的 JSON 版本。
- 农产品业务层读取标准 DuckDB 表；贵金属页读取已标准化且通过哈希校验的本地版本，均不直接依赖供应商原始列名。
- 每条标准行情记录包含来源、抓取时间、批次和质量标记。
- 免费数据仅用于个人研究；公开或商业再分发前需重新核对上游条款。

## DCE 交易日历（2026-11-24）

- 数据提供方：[Markets Open 年度公开数据](https://marketsopen.today/zh/datasets)，接口为 `https://marketsopen.today/data/china-exchange-holidays-<年份>.json`；使用其原始数据版本并保留署名，许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。当前下载版本 `2026.10.22-rc.1`，所取 DCE 年度范围为 2026-01-01—2026-12-31。
- 公告依据为 [大商所 2026 年度休市通知](https://www.dce.com.cn/dce/content/2025/ywggytz/18625615.html)。接口由第三方整理公告事实，不能将供应商字段 `source_type=official` 解释成该接口由交易所直接提供。大商所网页本次访问超时，已交叉核对其公告转发的休市日期；原始公告链接保留在清单中。
- 校验年份、许可、版本、DCE 身份和上海时区，要求完整七个常规节假日组、无重复／重叠、合理年度数量与一致复市日期；基于该年度公告范围，按周末规则补全每天开闭市标记。调休周末不成为交易日。源缺少年份或结构变化时拒绝导入；不以股票历史交易日历替代期货日历。
- 原始响应、逐日 CSV、获取时间、公告来源、许可和哈希存至 `data/reference/calendars/DCE/<年份>/versions/<SHA256>/`；数据库保留来源版本与 CSV 哈希。日历业务行在事务中替换，旧下载版本保留。明确同步才写入；页面浏览无网络调用，已有备份默认不再下载。
- 仅恢复 DCE 农产品日线选日／补数，不接夜盘和结算日期，也未将同一规则套到 SSE、SZSE、SGE 或跨境基金。年度数据不频繁更新，临时调整或跨年时明确校验；未知新年度不会自动生成。历史运行的 calendar/quality 审计不追溯修改。


## 贵金属当前接口（2026-11-02）

| 数据 | AKShare 接口 | 使用口径与状态 |
|---|---|---|
| SGE Au99.99／Ag99.99 | `spot_hist_sge` | date / close；分别为元／克、元／千克；已实测并采集至本地版本缓存 |
| 沪深 ETF／LOF 分类目录 | `fund_etf_category_sina` | 选择对应目录核验完整市场代码和类别；不是合同跟踪标的核验 |
| 场内基金日线 | `fund_etf_hist_sina` | 日期和收盘字段；未请求复权，未作为总回报或个人估值；公开 ETF／LOF 各一例通过 |

贵金属服务位于 `src/quantview/metals/`，不写入期货行情表。原响应在回溯窗口裁剪后与标准报价一起存于 `data/market/metals/` 的 JSON 版本，保存 endpoint、AKShare 版本、请求参数、抓取／可用时间、单位和质量；不同于原农产品 Parquet 路径。每次成功更新新建版本，失败保留上次成功版本并另记错误；读取校验内容哈希。

日期精度为日，不伪装实时报价；当日 18:10 前暂缓展示，当日历缺失时只提示自然日年龄，默认超过 7 日提醒陈旧。窗口指标按有效观测，不能称连续交易日；不提供基金 NAV／IOPV、溢价或个人总收益。行情更新入口为页面明确按钮及 `quantview metals sync`，现有农产品定时任务未扩展。

参考：[AKShare SGE 接口](https://akshare.akfamily.xyz/data/spot/spot.html)、[AKShare 基金接口](https://akshare.akfamily.xyz/data/fund/fund_public.html)、[SGE 日行情](https://www.sge.com.cn/sjzx/mrhq)。报价单位分别依据 [Au99.99 合约](https://www.sge.com.cn/cpfw/Au9999) 与 [Ag99.99 合约](https://sge.com.cn/cpfw/byjqxq?cplx=8&parent_cplx=0&pro_id=793738186796261376)；后者为现货即期合约，不能将观察价误当成个人可立即变现报价。实测证据与限制见 [11 月 2 日进度](PROGRESS_2026-11-02.md)。

## 贵金属后续来源要求

专项契约和官方参考见 [贵金属跟踪设计第 5 章](PRECIOUS_METALS_DESIGN.md#5-市场数据单位与时间)。下表是完整目标，除上述已交付子集外仍待接入与核验。

| 数据 | 权威依据／接入方式 | 必须核验 |
|---|---|---|
| 金银相关基金 | 管理人合同／NAV／公司行动＋已验收行情 adapter | ETF／LOF／期货型分类、未复权价格、净值所属与可用时间、单位 |
| 国内金价 | 上海黄金交易所真实产品与日行情 | Au99.99、SHAU 等身份；收盘／均价／集中定价不可混用 |
| 积存金 | 实际银行产品规则、用户渠道报价和本地确认 | 用户买入／赎回方向、费用是否已含、有效时段、余额单位 |
| 实物 | 用户批次、成色和实际适用回购渠道 | 参考材料价值与回购参考净额分开；无费用资料不默认零 |
| 国际金银与汇率 | 明确口径的数据商／权威汇率来源 | USD／金衡盎司、USD/CNY 方向、时点、节假日及延迟 |
| AU / AG | 上期所规则和真实日行情；现有期货 adapter 待专项验收 | 上期所日历、真实挂牌／到期、元／克与元／千克、日盘／夜盘归属 |

新闻报价只用于研究证据，不从报道自动填充日终市场表。无银行授权接口时采用受控手工记录，不自动登录银行；公开行情 AI 不接收个人数量、成本或原始账户文件。
