# 01 需求文档（Requirements）

## 背景

paperrss 的目标是把“LLM 工程相关论文追踪”从手工浏览变成稳定自动化流程，核心交付对象是 Slack 消息与本地报告文件。系统以 daemon 模式运行，必须同时覆盖日常推送、周报聚合、命令触发和健康可观测。

## 决策

### 目标与非目标

| 类型 | 编号 | 说明 |
| --- | --- | --- |
| 目标 | G-001 | 每日自动拉取 arXiv 论文并输出可读报告与 Slack 推送 |
| 目标 | G-002 | 每周自动聚合最近 7 天日报并输出周报与 Slack 推送 |
| 目标 | G-003 | 提供 Slack 命令通道用于健康检查、简报查看、强制重跑 |
| 目标 | G-004 | 提供本地健康检查接口，支持值班与排障 |
| 非目标 | NG-001 | 不做论文 PDF 全文解析，仅基于 metadata + abstract |
| 非目标 | NG-002 | 不做多租户隔离与权限体系 |
| 非目标 | NG-003 | 不提供 Web UI |

### 角色与场景

| 角色 | 场景 | 关心点 |
| --- | --- | --- |
| R-OPS（运维） | 守护进程运行、故障恢复 | 是否按时执行、是否可快速定位错误 |
| R-READER（读者） | Slack 查看日报/周报 | 信息是否有层次、是否可快速跳转到论文 |
| R-AGENT（AI 代理） | 按规范扩展功能 | 接口边界与状态语义是否明确 |

### 功能需求（FR）

| 编号 | 需求 |
| --- | --- |
| FR-001 | 系统必须以 daemon 方式并发运行四个循环：`daily_rss_loop`、`weekly_report_loop`、`socket_mode_loop`、`health_server_loop`。 |
| FR-002 | 日报调度必须按 `Asia/Shanghai` 计算触发时刻，且每天只执行一次（依赖 `schedule_state.last_daily_key`）。 |
| FR-003 | 日报流程必须执行：拉取 -> 分类 -> 作者增强 ->（可选）LLM brief -> 生成报告 -> Slack 推送 -> 状态落盘。 |
| FR-004 | 排序策略必须支持 `inference_acceleration` / `balanced` / `recent` 三种。 |
| FR-005 | `inference_acceleration` 默认策略必须优先提升 `is_inference_accel=true` 的论文。 |
| FR-006 | 周报调度必须按周日北京时间触发，并从最近 7 天日报聚合出周报。 |
| FR-007 | 周报生成必须支持一次“仅计算不写盘”路径，用于 LLM synthesis 前置统计。 |
| FR-008 | Socket Mode 命令必须支持 `-ping`、`-brief`、`-force`、`-help`。 |
| FR-009 | `-force` 命令必须支持异步重跑，并在完成后回帖汇报结果。 |
| FR-010 | `/healthz` 必须返回时间快照 + daily/weekly/pingpong 三类运行状态。 |
| FR-011 | 推送必须具备幂等去重能力：重启后不重复发送已推论文。 |
| FR-012 | 系统必须将状态持久化到 `storage/data/` 下 JSON 文件，文件不存在时可自动初始化。 |
| FR-013 | 系统必须允许关闭 LLM、作者增强等可选能力，并保持主流程可运行。 |
| FR-014 | 日报推送必须采用“摘要先行 + 单篇详情逐条发送”的阅读模式。 |

### 非功能需求（NFR）

| 编号 | 需求 |
| --- | --- |
| NFR-001 | 可观测性：关键阶段必须写日志（启动、调度、推送、错误、重试）。 |
| NFR-002 | 鲁棒性：网络异常、单条推送失败、单篇作者抓取失败不能导致整轮崩溃。 |
| NFR-003 | 可恢复性：中途失败后重启，去重状态与已见状态必须可继续使用。 |
| NFR-004 | 可配置性：所有核心行为由 `config.example.json` 定义，运行时从 `storage/config.json` 读取。 |
| NFR-005 | 兼容性：允许保留历史字段（例如 `slack_push_workers`、`slack_preserve_order`）但不得影响主流程。 |
| NFR-006 | 可扩展性：新命令与新排序策略应可在不重构主流程的情况下增量加入。 |

### 验收标准（AC）

| 编号 | 验收说明 |
| --- | --- |
| AC-001 | daemon 启动后，4 个循环线程都可运行，`/healthz` 返回 200。 |
| AC-002 | 在配置有效时，日报到点触发且 `schedule_state.last_daily_key` 更新。 |
| AC-003 | 在配置有效时，周报到点触发且 `schedule_state.last_weekly_key` 更新。 |
| AC-004 | 默认排序下，`is_inference_accel=true` 的同分论文排序高于 `false`。 |
| AC-005 | `parse_daily_report` 对文件读异常返回安全空结构，不中断周报流程。 |
| AC-006 | 周报生成 `write=False` 时不创建/覆盖文件，`write=True` 时写盘。 |
| AC-007 | `-force` 触发后能收到“started”与“done”两次反馈。 |
| AC-008 | 去重开启时，重复运行不会重复推送同一篇论文。 |

## 约束

- 本文档对应当前 `main` 实现，不假设未来未落地能力。
- 接口定义与字段语义以 [03-system-design.md](03-system-design.md) 和 [04-config-reference.md](04-config-reference.md) 为准。
- 架构边界与依赖关系以 [02-architecture-design.md](02-architecture-design.md) 为准。

## 示例

- 示例 A（日报触发）：
  在北京时间到达 `daily_report_time_bjt` 后，daemon 执行 `arxiv_rss_assistant.run`，产出 `storage/reports/YYYY-MM-DD.md`，并发送 Slack 概览 + 逐篇消息。

- 示例 B（命令重跑）：
  用户在频道 `@bot -force` 后，系统清理当日 state/report，后台触发一次 `force_push` 运行，并在同线程回帖结果。

## 验收

- 任何需求编号（FR/NFR/AC）都能在设计文档中找到落地约束。
- 基于本文档可独立产出实现任务清单，无需再做关键产品决策。
