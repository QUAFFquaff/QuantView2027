# QuantView2027 看板、邮件与复盘设计

> 版本：v0.1｜更新：2026-10-24｜状态：目标设计，新增功能尚未实现
> 上位文档：[开发设计](DEVELOPMENT_DESIGN.md)｜开发顺序：[实施与验收计划](IMPLEMENTATION_PLAN.md)

## 1. 产品选择与使用流程

个人看板为主入口，邮件为日终提醒，微信小程序暂缓。当前只有 LH / JD / C 日频研究，不为新增展示渠道引入盘中行情、账户连接或下单能力。

日常流程：

1. 日终任务完成，生成带版本的研究快照；数据失败时生成异常快照。
2. 用户阅读短摘要，需要时打开本地看板检查合约、价差和证据。
3. 有已激活纪律时，查看明确条件的检查结果；没有纪律时显示“未配置”。
4. 选择记录研究判断、暂不行动或外部手工决策，指定理由与复盘日期。
5. 到期复盘时对照当时快照，分别评价证据变化与规则执行。
6. 每周查看记录覆盖、未知条件与待复盘项，决定是否需要改研究方法。

产品效果先通过使用与记录质量评估。真实收益、风险调整绩效或策略有效性需要另外的数据与研究，不以看板访问次数或 AI 自评替代。

## 2. 页面与交互要求

### 2.1 今日总览（UI-01）

按以下顺序展示，避免首页成为大表格或 AI 长文：

| 区域 | 内容 | 状态与动作 |
|---|---|---|
| 数据状态 | 选定交易日、截至时间、最新完整日、覆盖、快照版本 | 部分 / 失败显著提示，能打开缺口详情 |
| 三品种卡片 | 名称、真实主力、结算字段值、同合约日变化、链式 5/20 日指标、量仓变化 | 分别展示单位、质量与窗口状态；换月明确标记 |
| 重要变化 | 最多 5 项确定性变化，按固定优先级排序 | 每项给出前后值、比较日期、合约 / 合约对及证据链接 |
| 规则条件 | 满足 / 未满足 / 无法判断 / 未配置 | 展开显示规则版本与原始证据；不使用“推荐买入” |
| 待复盘 | 到期记录数及入口 | 无到期项时简短说明 |
| AI 研究 | 当前快照绑定的多空论证、反证、推翻条件 | 默认折叠；自评分只在详情出现 |

在约 390px 宽度按单列排列，表格可横向滚动；电脑端三卡并排。数据是 T 日就写 T 日，不能仅因今天打开而称为“今日最新”。无有效数据时展示原因、最近快照、系统状态和个人记录入口。

页面顶部可用轻量布局表达：

```text
交易日 / 快照版本 / 数据质量 / 最近完整日
生猪卡片       鸡蛋卡片       玉米卡片
重要变化（含证据）
我的规则条件                 到期复盘
AI 研究详情（展开查看）
```

### 2.2 品种研究（UI-02）

- 选择真实合约；真实 OHLC 齐备时可画 K 线，缺失时不伪造。
- 主力历史必须按真实合约分段，换月处断线；显示旧 / 新主力代码及两者当日价格。
- 成交量与持仓量清楚标明名称、单位及日期，不从总持仓推断主动买卖方向。
- 链式变化指标、单合约价格和价差分别展示，不称作策略收益或净值。
- 统计窗口显示 `window / sample_count / warmup / missing_dates`；预热结果与完整窗口分开标识。
- 展示 Evidence Pack、数据来源与替代结算字段提示，再展示 AI 解释。

### 2.3 期限结构（UI-03）

- 当前日与一个选定历史交易日对照，交割月份排序不因主次身份改变。
- 价差方向固定为近月减远月，不能把“主力减次主力”当成同义词。
- 显示两腿代码、真实 / 替代结算、流动性、样本量和窗口定义。
- 固定真实合约对历史独立计算；主次角色改变或合约对更换时不连成同一价差序列。
- 不能用 60 日分位暗示跨年份同季节可比；农业季节性比较属于后续独立研究。

### 2.4 日报与系统状态（UI-04）

- 日期与快照版本共同选择，区分“当时发布”“事后补建”“后续修订”。
- 分开显示采集、数据质量、AI、快照发布、邮件状态。邮件失败不把数据说成失败。
- 历史页默认打开当日最早有效发布版本；另有“最新修订”入口，切换必须可见。
- 开放缺失区间选择补跑；启动不自动补数、发邮件或调用 AI。
- 即使无行情数据，也可查看失败运行、个人记录、邮件预览和备份状态。

### 2.5 纪律与记录、复盘（UI-05 / UI-06）

- 纪律列表、草稿编辑、预览差异、显式激活、历史版本只读。
- 记录表单绑定快照与当时纪律版本；没有可用快照可保存观察笔记，但规则检查为无法判断。
- 记录提交前预览；验证失败或写锁占用保留输入，不误显示“保存成功”。
- 复盘同时展示原始记录、后续修订、原快照和选定的后续快照。
- 原始理由与复盘结论分栏，避免事后修改原始理由；收益方向与纪律执行分开评价。

## 3. 研究快照与版本一致性

### 3.1 三种存储职责

| 存储 | 可变性 | 用途 |
|---|---|---|
| 标准行情 / 指标表 | 按主键修正与重算 | 最新查询、指标计算 |
| 原始响应 Parquet | 追加 | 上游数据追溯 |
| ResearchSnapshot 与报告版本 | 发布后不可变 | 当时所见、跨渠道一致、记录引用 |

已有按日期覆盖的 Markdown 仅可作为“最新副本”。看板、邮件与个人记录都以 `snapshot_id` 为关联依据，不以一个会变的文件路径或“同日最新 AI”作为依据。

### 3.2 `ResearchSnapshot` 契约（SNAP-01）

每次候选快照至少包含：

| 字段组 | 必须保存 |
|---|---|
| 身份 | `schema_version`、`snapshot_id`、`trade_date`、`revision`、`run_id`、`created_at`、`available_at`、`timezone` |
| 版本语义 | `generation_mode=scheduled/manual/backfill/imported`、`supersedes_snapshot_id`、`revision_reason` |
| 状态 | `data_status`、逐品种质量、`ai_status`、日历来源与覆盖 |
| 市场证据 | 品种指标、真实合约、固定合约对、窗口 / 缺口、来源、原始批次、替代价标记 |
| 分析 | 完整 Evidence Pack、其哈希、当前快照引用的 AI 结果及各自版本 ID |
| 计算版本 | Git commit（可用时）、dirty 标记、应用版本、指标版本、提示词内容哈希、模型标识、脱敏业务配置哈希 |
| 共享摘要 | 结构化变化列表、规则检查版本引用、`summary_version` |
| 归档 | 规范化内容 SHA-256、证据文件相对路径、日报路径 |

规范化规则：稳定字段排序、明确数字精度、非数值为 `null`、ISO 时间；内容哈希排除 `snapshot_id/run_id/created_at/available_at/revision` 等运行身份，但包含影响结果的数据、质量、分析及业务配置版本。密钥和 SMTP 收件人不参与研究内容。

相同内容可复用已发布快照，新增运行关联记录；不同内容创建新快照。重新调用 AI 得到不同文本即是新内容，不能覆盖旧分析。所有路径位于项目受控目录，不接受用户任意路径写入。

### 3.3 发布流程（SNAP-02）

1. 在写锁内保存数据及本轮分析版本，准备完整候选快照。
2. 写临时归档，校验哈希与结构，分配日期内递增 `revision`。
3. 在同一文件系统原子改名到唯一版本目录。
4. 在事务中登记发布索引并关联运行；索引提交前，该版本不对 UI / 邮件可见。
5. 最新 Markdown 副本随后原子更新；副本失败不修改已发布快照。
6. outbox 只接受 `published` 版本；失败运行允许发布没有市场结论的异常版本。

文件系统与 DuckDB 无法构成一个事务：启动恢复应识别“文件已写但索引未提交”的孤立目录、缺文件索引和哈希不符，隔离并记录，不静默发布半成品。不在已发布目录内追加改变内容的文件；日报与证据应一起准备。

建议路径（待实现）：

```text
reports/archive/{trade_date}/{snapshot_id}/snapshot.json
reports/archive/{trade_date}/{snapshot_id}/daily.md
reports/archive/{trade_date}/{snapshot_id}/evidence/{product}.json
reports/daily/{YYYY}/{MM}/{trade_date}.md  # 最新兼容副本
```

### 3.4 事前与事后区分（SNAP-03）

- 回填 / 导入日期早于实际生成时间，`available_at` 使用真实生成时间，不能回填成历史日期。
- 迁移前已有报告以 `imported` 导入，标记“原始发布时间不可核验”，保留导入时间和原文件哈希。
- 手工记录保存 `recorded_at`；外部决策发生时间另存 `decision_at`。后补记录显著标记，不冒充事前记录。
- 事前纪律检查仅可引用 `available_at <= decision_at` 的有效快照与纪律版本；事后重评另存为复盘，不回写当时检查结果。
- 旧数据修正后，新版本显示修订原因；引用旧快照的记录仍指向原版本。

## 4. 共享摘要与规则检查

### 4.1 `DailySummary`（SUM-01）

输入为一个已发布快照，输出给 UI、Markdown 与邮件共用。通知时的待复盘数可另按 `generated_at` 获取，但必须单独标记，不混入不可变市场摘要。

```text
snapshot_id, trade_date, data_status, summary_version
products[]: product, main_contract, metrics, quality, windows
changes[]: kind, priority, subject, previous_value, current_value,
           comparison_trade_date, evidence_refs[], explanation
rule_checks[]: discipline_version_id, rule_id, status, reason, evidence_refs[]
ai_refs[]: analysis_version_id, status
```

摘要由确定性代码生成，不新发一次 AI 请求。变化的默认顺序：

1. 数据缺失 / 修订 / 替代价；
2. 主力切换、流动性状态变化；
3. 已配置条件的状态变化；
4. 其他指标变化，按显式配置排序。

上限 5 项，同优先级按品种、合约与类型稳定排序。前值不足、窗口不足或合约对不同，输出“无法比较”的原因，不能填零。没有触发事件时可显示“未触发已配置变化条件”，不能推断“市场无风险”。

### 4.2 规则检查的能力边界（RULE-01）

`纪律.md` 是用户策略的文字说明；机器只执行配套结构化条件中已明确登记的字段和比较算子，不让 LLM 将任意自然语言编译成交易策略。

每条条件包含 `rule_id/description/scope/metric/operator/value/window/required_quality`；一期支持字段白名单及 `gt/ge/lt/le/eq`，不支持 Python 表达式、任意 SQL 或动态代码。文字里无法计算的条件保留为人工项。候选快照发布前计算当时已激活版本的检查，随快照固化；此后对新纪律版本的试算明确标为“事后试算”，不修改原摘要。

状态：

| 状态 | 定义 |
|---|---|
| `met` | 所需数据有效且条件成立 |
| `not_met` | 所需数据有效且条件不成立 |
| `unknown` | 缺字段、窗口不足、质量不合格或仅有事后数据 |
| `not_configured` | 没有适用的已激活条件 |

系统只展示逐条结果，不把“所有条件满足”自动解释成应该开仓。用户未提供规则时保持 `not_configured`，不默认启用节目温度系统、凯利仓位或示例参数。

## 5. 纪律版本、手工决策和复盘

### 5.1 纪律生命周期（JOURNAL-01）

- `draft → active → retired`。草稿可编辑，激活后内容不可变；修改必须复制为新版本。
- 一期仅有一个全局当前激活版本，内部按品种 / 研究类型定义适用范围。激活新版本时原版本退役，已有记录保留原引用。检查过去决策时按真实激活 / 退役区间选版本，禁止回溯激活时间。
- 保存文字正文、结构化条件、内容哈希、创建 / 激活 / 退役时间和变更原因。
- 激活仅表示用户选择用这套规则，不代表系统验证其盈利能力。UI 应明确需要用户点击激活。
- 同一草稿若编辑期间被其他会话修改，通过修订号冲突检查拒绝覆盖。

### 5.2 手工决策记录（JOURNAL-02）

必填：品种 / 真实合约或合约对、记录类型、理由、反证或推翻条件、复盘到期日。类型为 `observation/no_action/external_decision`；外部决策只记录文字性质及方向，不生成可执行订单，也不要求账户、凭据或手数。

记录可绑定一个快照与纪律版本。若两者缺失，允许保存笔记，但不能判为已通过规则检查。需要保存的字段详见第 8 章。

提交后内容不可覆盖。更正时创建新版本并填写原因；重复点击使用客户端提交 ID 去重。作废产生追加事件而不是物理删除。个人隐私清除应通过独立、显式的本地删除 / 备份清理流程，不混入日常编辑。

“符合纪律”区分：机器条件结果、用户对人工项的确认、最终 `compliant/violated/unknown/not_applicable`。只有适用项全部有依据、人工项明确确认且无违反项时才可标为符合；存在未知时保留未知，不让 AI 自动裁决。

### 5.3 独立复盘（REVIEW-01）

复盘关联记录的特定版本，并保存后续快照、复盘时间、证据评价、执行评价、经验与后续动作。

- 证据评价：`supported/invalidated/mixed/insufficient_data`，有具体引用。
- 执行评价：`compliant/violated/unknown/not_applicable`，和盈亏方向无直接绑定。
- 如果用户记录后续价格变化，标为“观察期价格变化”，注明合约、起止日期和结算口径；不称实际交易盈亏。
- 到期日采用明确自然日日期，允许周末手工复盘；系统用上海日期判断是否到期，不隐式挪到交易日。
- 已提交复盘同样追加修订，保留原内容。没有后续数据时可保存“数据不足”，记录仍可设置下次复盘日。

### 5.4 周度汇总（REVIEW-02）

按 `Asia/Shanghai` 周一 00:00 至下周一 00:00 的半开区间汇总，从下周一开始，在首次实际运行的日任务结束后生成上一周摘要；周一休市或电脑关机时顺延，不另设周末常驻任务。一次运行默认只发送最近一个完整周，缺失的更早周可手工生成且不补发。明确周次、生成时间、纳入记录 ID 和修订版本。

只汇总：观察 / 暂不行动 / 外部决策数量、符合 / 违反 / 未知数量、到期已复盘与逾期未复盘数量、反复出现的数据缺口和用户标记问题。

- 纪律执行率：`符合数 / (符合数 + 违反数)`，未知和不适用单列；分母为零显示无样本。
- 到期复盘完成率：截至生成时点已完成的到期记录数 / 同一批到期记录总数，明确统计截止点。
- 不展示策略胜率、投资收益或“AI 预测准确率”。只有方向变化的研究样本不能当成交易样本。

## 6. 邮件与投递设计

### 6.1 内容与发送策略（MAIL-01）

默认 `enabled=false`，开发先实现本地 HTML / 纯文本预览，测试使用假投递器。实际发送仅在用户配置发件人与收件人并启用后运行。

| 类型 | 触发 | 内容 | 去重 / 补跑 |
|---|---|---|---|
| 日终摘要 | 当前目标日截止时发布完整快照，或就绪重试已结束仍为部分 | 日期、质量、最多 5 项变化、规则状态、详情入口 | 每交易日、收件人集合、类型首发一次 |
| 失败通知 | 任务已启动且就绪重试结束，无可用目标日数据或发布失败 | 原因摘要、最近完整日期、处理入口 | 同一故障事件一次 |
| 恢复通知 | 已通知的故障恢复 | 恢复日期 / 版本及仍有缺口 | 同一故障事件恢复一次 |
| 周度复盘 | 生成上一完整周的复盘摘要 | 第 5.4 节统计、待处理项 | 每周、收件人集合一次 |

部分日报标题写明“数据不完整”；无数据时不发送带看多 / 看空结论的日报。无变化日仍可发送一封短摘要，用户可配置仅异常提醒。AI 失败不触发数据失败通知，在摘要注明“AI 未生成”。

日终截止前的临时失败只在系统状态展示，避免每次重试发一封。历史 `backfill/rebuild/import` 默认不通知；启动补跑历史也不补发旧邮件。已发日报后修订仅在看板提示，不自动追加修订邮件；用户显式选择发送修订版时，新建带修订标识的投递事件。

主题示例：`[QuantView][2026-10-23][数据完整] 日终摘要`，不使用“必买”“胜率”等交易诱导词。邮件正文约一分钟阅读量，数据可自包含，默认不附大文件、不加载外部图片、不含追踪像素。

### 6.2 地址与运行条件（MAIL-02）

- `dashboard_url` 未配置或是 localhost / 私网不可访问地址时，默认省略远程链接，写明“详情见本地工作台”。配置私网地址时必须明确标为仅相应网络可访问。
- 配置远程地址并不代表已部署；邮件不得包含 API Key、SMTP 密码、账户凭据或秘密查询参数。
- 邮件只用于日终研究通知，不作为止损、保证金或盘中风险告警。
- 电脑关闭、程序未启动或 SMTP 本身不可用时，不能依赖同一通道报告所有故障；下次启动展示未运行 / 待投递状态。

### 6.3 outbox 状态机与重复投递（MAIL-03）

```text
pending → sending → sent
                  → failed → pending（有界重试）
                  → unknown（可能已被服务器接收）
pending / failed → cancelled（用户取消）
unknown → sent 或 pending（用户核对后明确处理）
```

`sent` 仅指 SMTP 服务器确认接收，不等于收件箱已送达或用户已阅读。

- `dedup_key = SHA256(channel + recipient_set_hash + kind + logical_period + event_id)`；日终用交易日、周报用周起点、故障 / 恢复用持久化 incident ID、手工修订用独立 event ID。原子唯一约束防止两个进程创建同一逻辑发送。
- `payload_hash` 单独保存；同一逻辑发送在 pending 状态可以明确取消后换新版，sent 后不得因内容变化自动重发。
- 投递前在短事务领取并保存尝试次数、稳定 `Message-ID`、状态和开始时间，然后释放数据库写锁再访问 SMTP。
- 连接前失败或明确拒绝可按配置重试；正文提交后连接中断 / 等待确认超时为 `unknown`，不盲目自动重发。
- 进程崩溃遗留 sending 无法证明没发送，恢复为 unknown，展示人工核对入口。不能声称 SMTP 端到端 exactly-once。
- 每次重试记录独立 attempt；达上限留在 failed，本地可见。邮件失败不改变数据和 AI 状态，也不通过失败邮件递归通知邮件失败。
- TLS 校验启用；支持显式 SSL 或 STARTTLS，凭据通过环境读取，不做明文降级。错误日志脱敏。

## 7. 数据质量与异常行为

| 场景 | 页面 / 快照 | 邮件 / 记录 |
|---|---|---|
| 目标日三品种覆盖但缺活跃合约 | 显示 partial 及缺失合约，不宣称全市场完整 | 依赖缺口的条件 unknown；部分摘要明确标记 |
| 结算字段由收盘价填充 | 保留标记，价格与价差标注替代口径 | 要求真实结算的条件 unknown；摘要带提示 |
| 历史窗口不足 / 缺交易日 | 显示样本数、预热、缺口 | 不伪造 20 / 60 日有效信号 |
| AI 缺 Key、超时或输出无效 | 数据仍可用；AI 为空并给原因 | 不拉取旧 AI 冒充本次结果 |
| 新旧主力交替 | 分段真实价格，旧 / 新代码可见 | 不产生由换月价格差制造的涨跌事件 |
| 日历范围外或不可信 | 状态显示 unknown，已有数据可浏览 | 暂停无人值守发送；手动生成带警告 |
| 无任何行情 / 数据库为空 | 系统状态和个人记录仍可访问 | 可保存未绑定笔记；无市场判断邮件 |
| 写锁占用 | 短时等待后明确提示 | 个人表单保留输入，发送状态不假报成功 |

完整性检测必须针对目标日、逐品种和合约集合，不能用本次接受的历史总行数替代。预期集合来自目标时期已核验合约注册信息；发现来源失败时不能把缩小后的集合重新定义为完整。规则条件缺少可靠预期集合时降为 unknown。

## 8. 新增存储模型与迁移

以下是逻辑表契约，正式 DDL 应在对应开发任务中提交。快照相关的检查结果可在同一发布事务中登记，避免依赖未提交的快照外键；重复内容复用已发布检查结果。时间字段使用 UTC 的 `TIMESTAMPTZ`，界面转换到上海时间；旧表时间无时区时不能猜测，应记录迁移时区假设并保留原值。

| 表 | 主键 / 唯一约束 | 核心字段 |
|---|---|---|
| `schema_migration` | `version` | checksum、applied_at、app_version |
| `exchange_calendar` | exchange + trade_date | is_trading_day、source、source_version、verified_at、coverage；需要覆盖非交易日 |
| `research_snapshot` | snapshot_id；trade_date + revision 唯一 | run_id、状态、generation_mode、available_at、payload_hash、archive_path、supersedes、reason |
| `run_snapshot` | run_id + snapshot_id | 相同内容重跑复用快照时保留每次运行关联 |
| `analysis_version` | analysis_version_id | snapshot_candidate_id、product、完整输入 / 输出、input_hash、model、prompt_hash、created_at；候选 ID 为内部生成身份，发布时显式关联最终快照 |
| `discipline_version` | discipline_version_id | discipline_id、version、status、markdown、conditions_json、content_hash、时间与变更原因 |
| `rule_evaluation` | evaluation_id；snapshot_id + discipline_version_id + rule_id 唯一 | status、reason、evidence_refs、evaluated_at；已提交结果不可变 |
| `decision_record` | record_id | 创建时间与当前版本指针；指针更新不能删除旧版本 |
| `decision_version` | record_id + version；submission_id 唯一 | decision_at、recorded_at、record_type、product、subject、snapshot_id、discipline_version_id、evaluation_ids、理由 / 反证、复盘日、执行评价、late_entry、修订 / 作废原因 |
| `review_version` | review_id + version；submission_id 唯一 | decision_version_ref、reviewed_at、followup_snapshot_id、证据评价、执行评价、笔记、下次复盘日、修订原因 |
| `weekly_digest` | digest_id；period_start + revision 唯一 | period_end、generated_at、纳入版本引用、统计、内容哈希；已生成版本不可覆盖 |
| `notification_incident` | incident_id | error_fingerprint、首次 / 最后发现时间、notified_at、resolved_at、recovery_notified_at |
| `notification_outbox` | notification_id；dedup_key 唯一 | kind、period、snapshot_id / digest_id / incident_id、收件人集合哈希、payload_path / hash、status、message_id、attempt_count、timestamps |
| `notification_attempt` | attempt_id | notification_id、started_at、finished_at、结果、脱敏错误、服务器确认信息 |

所有引用在服务层和可用数据库约束下校验存在性；不允许引用不同日期或不匹配品种的证据而无明确说明。复盘引用记录的特定版本，记录修订后不自动重算历史复盘。

迁移规则（STORE-01）：

1. 获取全局写锁并阻止新任务；关闭活跃连接后做一致性数据库备份及档案清单备份。
2. 校验迁移版本和脚本 checksum，事务内增加表 / 字段，避免破坏现有查询。
3. 将现有日报 / AI 导入为 legacy 来源；无法还原的证据标记不可核验，不伪造快照完整性。
4. 迁移失败回滚事务，保留备份；涉及文件导入的步骤可幂等续跑，并记录检查点。
5. 旧版应用不得打开已不兼容的新 schema；启动检查版本并给出说明。
6. 当前 `init-db` 只有 `CREATE TABLE IF NOT EXISTS`，不是完整升级器；版本迁移属于本轮新增任务。

个人数据可导出 Markdown / JSON（供人阅读与备份），快照保留结构化 JSON。恢复需同时检查数据库引用、档案存在性及哈希，验证后才切换为当前库。备份、outbox 正文和个人纪律必须加入忽略规则；恢复功能不自动重发历史 outbox。

## 9. 模块与配置契约

### 9.1 模块边界（均为计划新增）

| 模块 / 服务 | 输入 | 输出与边界 |
|---|---|---|
| `operations/calendar.py` | exchange、as_of、已核验日历 | expected_trade_date、缺失区间、日历可信状态 |
| `data/migrations/` | 旧 schema 与档案 | 可追溯升级；失败回滚 |
| `research/snapshots.py` | 确定性指标、证据、本轮 AI | 不可变快照发布与读取，不发邮件 |
| `research/summary.py` | 单个快照、摘要配置 | DailySummary，不重新获取行情或调用 AI |
| `discipline/service.py` | 用户规则版本、快照 | 激活记录与逐条规则检查，不输出订单 |
| `journal/service.py` | 表单、版本引用、submission_id | 追加个人记录 / 复盘、冲突校验 |
| `reviews/weekly.py` | 周区间、个人记录与复盘 | 带分母的周度摘要 |
| `notifications/service.py` | 发布快照 / 周报 / 故障事件 | 创建 outbox、发送策略、去重 |
| `notifications/smtp.py` | 固定邮件 payload、SMTP 环境配置 | 投递结果，不访问行情表或 AI |
| `ui/` | 上述服务返回对象 | 页面与表单，不直接修改业务表 |

先把 `ui/app.py` 的查询提取到只读查询服务，再按需要拆页面；无需为了目录完整而建立大量空文件。短期用统一写锁支持本地单人操作；真正并发服务或远程多用户需要重新评估存储，不直接把 DuckDB 暴露为服务后端。

### 9.2 配置示例（仅设计，当前不可直接启用）

```yaml
runtime:
  timezone: Asia/Shanghai
calendar:
  exchange: DCE
  source: verified_local_file
  path: data/reference/dce_calendar.csv
snapshots:
  archive_root: reports/archive
  preserve_versions: true
summary:
  max_changes: 5
  version: v1
journal:
  enabled: true
  export_root: data/personal/exports
notifications:
  email:
    enabled: false
    daily_mode: summary  # summary / exceptions_only
    send_backfill: false
    dashboard_url: null
    dashboard_access: local  # local / private_network / remote
    smtp_host_env: QUANTVIEW_SMTP_HOST
    smtp_port_env: QUANTVIEW_SMTP_PORT
    smtp_security_env: QUANTVIEW_SMTP_SECURITY
    smtp_username_env: QUANTVIEW_SMTP_USERNAME
    smtp_password_env: QUANTVIEW_SMTP_PASSWORD
    from_env: QUANTVIEW_MAIL_FROM
    to_env: QUANTVIEW_MAIL_TO
    max_attempts: 3
    retry_delays_seconds: [60, 300]
```

SMTP 安全方式必须显式为 `ssl/starttls`；环境字段不存在或配置非法时显示配置错误，不猜测收件人或降级明文。配置校验拒绝未知字段或无效值，避免“填了参数但程序没有使用”。重试在独立通知任务执行，任务未常驻时在下次明确触发继续，不靠页面轮询偷偷发送。

就绪重试的间隔和截止点应在验证上游更新时间后设定；超出截止点发布部分 / 异常结果并结束，避免无限等待。需补充对应配置及验证测试，不能沿用 SMTP 重试配置。

### 9.3 命令行为（计划接口，尚未实现）

| 命令 | 默认行为 |
|---|---|
| `quantview run-daily` | 延用当前命令；新增日历 / 质量 / 快照；邮件仅在显式启用配置后按策略运行 |
| `quantview backfill` | 默认不发邮件、不运行 AI；明确历史合约覆盖 |
| `quantview preview-email --snapshot ID` | 本地渲染预览，绝不发送 |
| `quantview retry-notifications` | 仅领取 pending / 可重试 failed；unknown 不自动处理 |
| `quantview export-journal` | 导出个人记录，排除凭据与 outbox 收件人 |
| `quantview backup` / `restore --preview` | 一致性备份 / 恢复预检；真正恢复需明确指定备份 |

## 10. 交付边界与后续选择

本轮开发文档不包含具体已激活的投资纪律、不预填真实邮件收件人，不注册系统任务。功能实现可在这些信息缺失时推进：规则保持未配置，邮件保持关闭并支持预览。

P0 至 P3 完成并试运行后，用真实使用记录评估移动需求：是否经常无法在电脑查看；是否必须在手机记录；邮件是否已足够。只有手机网页仍不能满足明确需求时，再单独制定小程序方案。
