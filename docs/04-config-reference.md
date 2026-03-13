# 04 配置字典（Config Reference）

## 背景

配置项已扩展到 52 个，且分布在 daemon、RSS pipeline、Socket 命令与周报聚合多个子系统。为避免“README 片段化描述导致误配”，本文件作为唯一权威配置说明。

## 决策

- D-001：本文件是 `config.example.json` 的权威解释。
- D-002：字段按系统域分组，不按代码文件分组。
- D-003：每个字段必须声明：类型、默认值、必填条件、生效范围、风险等级、是否弃用。

### 4.1 数据源与推送触发

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `slack_webhook_url` | string | `https://hooks.slack.com/services/xxx/yyy/zzz` | 是（需推送时） | daily + weekly push | 高 | 否 | Slack Incoming Webhook 地址。 |
| `slack_when` | enum(`any`\|`relevant`) | `relevant` | 否 | daily push | 中 | 否 | `relevant` 仅在有相关论文时推送。 |
| `force_push_date` | string(`YYYY-MM-DD`) | `` | 否 | daily push | 中 | 否 | 当值等于当天时强制推送，绕过去重。 |
| `rss_categories` | string[] | `["cs.LG","cs.AI","cs.CL","cs.DC","stat.ML"]` | 否 | RSS fetch | 中 | 否 | arXiv 分类查询集合。 |
| `rss_max_results` | int | `250` | 否 | RSS fetch | 中 | 否 | 单次 Atom 拉取上限。 |

### 4.2 状态与目录

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `rss_state` | string(path) | `storage/data/state.json` | 否 | daily pipeline | 中 | 否 | 增量状态（`last_run/seen_ids`）。 |
| `subscription_store` | string(path) | `storage/data/subscriptions.json` | 否 | daily pipeline | 中 | 否 | 额外 seen_ids 存储。 |
| `push_state` | string(path) | `storage/data/push_state.json` | 否 | daily pipeline | 高 | 否 | 推送去重状态。 |
| `push_state_retention_days` | int | `14` | 否 | daily pipeline | 中 | 否 | `pushed_by_date` 保留天数。 |
| `rss_output_dir` | string(path) | `storage/reports` | 否 | daily report + brief 命令 | 低 | 否 | 日报输出目录。 |
| `weekly_output_dir` | string(path) | `storage/reports` | 否 | weekly report | 低 | 否 | 周报输出目录。 |
| `schedule_state` | string(path) | `storage/data/schedule_state.json` | 否 | daemon scheduler | 中 | 否 | 保存 `last_daily_key/last_weekly_key`。 |

### 4.3 调度配置

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `report_modes` | string[] | `["daily","weekly"]` | 否 | daemon scheduler | 中 | 否 | 启用日报/周报循环。 |
| `daily_report_time_bjt` | string(`HH:MM`) | `09:00` | 否 | daily scheduler | 中 | 否 | 日报触发时刻（BJT）。 |
| `weekly_report_time_bjt` | string(`HH:MM`) | `18:00` | 否 | weekly scheduler | 中 | 否 | 周报触发时刻（BJT，周日）。 |

### 4.4 Slack 命令通道

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `slack_bot_token` | string | `xoxb-...` | 是（启用命令时） | socket mode + slack api | 高 | 否 | Bot token。 |
| `slack_app_token` | string | `xapp-...` | 是（启用命令时） | socket mode | 高 | 否 | App-level token。 |
| `slack_channel_id` | string | `C0123456789` | 是（`-brief`/healthcheck CLI） | cmd toolkit + healthcheck | 中 | 否 | 频道 ID。 |
| `ping_text` | string | `ping` | 否 | healthcheck CLI | 低 | 否 | ping 触发词。 |
| `pong_text` | string | `pong` | 否 | healthcheck CLI | 低 | 否 | pong 回复词。 |
| `ping_poll_interval_seconds` | int | `10` | 否 | healthcheck CLI | 低 | 否 | 轮询间隔。 |
| `ping_state` | string(path) | `storage/data/healthcheck_state.json` | 否 | healthcheck CLI | 低 | 否 | 健康检查命令状态文件。 |
| `cmd_reply_in_thread` | bool | `true` | 否 | socket mode | 低 | 否 | 命令回复是否在线程内。 |

### 4.5 健康与日志

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `health_host` | string | `127.0.0.1` | 否 | health server | 低 | 否 | 健康接口监听地址。 |
| `health_port` | int | `8080` | 否 | health server | 中 | 否 | 健康接口端口。 |
| `log_level` | string | `INFO` | 否 | daemon | 低 | 否 | 日志等级。 |
| `log_file` | string(path) | `logs/app.log` | 否 | daemon | 低 | 否 | 日志文件输出路径。 |

### 4.6 排序与分类

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sort_priority` | enum | `inference_acceleration` | 否 | ranking | 中 | 否 | 可选 `inference_acceleration/balanced/recent`。 |
| `classify_workers` | int | `8` | 否 | classify stage | 中 | 否 | 分类并发数。 |

### 4.7 作者增强

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `author_enrich` | bool | `true` | 否 | author enrich | 中 | 否 | 开关作者信息抓取。 |
| `author_cache` | string(path) | `storage/data/author_cache.json` | 否 | author enrich | 低 | 否 | 作者增强缓存。 |
| `author_enrich_max_papers` | int | `60` | 否 | author enrich | 中 | 否 | 单次增强论文上限。 |
| `author_enrich_timeout_seconds` | int | `8` | 否 | author enrich | 中 | 否 | 单篇抓取超时。 |
| `author_enrich_workers` | int | `8` | 否 | author enrich | 中 | 否 | 并发 worker 数。 |

### 4.8 LLM Brief 与打分

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `interest_topics` | string[] | `["大模型训练",...]` | 否 | llm brief prompt | 中 | 否 | 用户兴趣点列表。 |
| `llm_brief_enabled` | bool | `false` | 否 | llm stage | 中 | 否 | 启用 Qwen brief。 |
| `llm_brief_api_key` | string | `sk-...` | 是（启用 LLM 时） | llm stage + weekly synthesis | 高 | 否 | Qwen API key。 |
| `llm_brief_base_url` | string(url) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 否 | llm stage | 中 | 否 | Qwen 兼容接口基址。 |
| `llm_brief_model` | string | `qwen-long` | 否 | llm stage | 中 | 否 | 日报 brief 模型。 |
| `llm_brief_cache` | string(path) | `storage/data/llm_brief_cache.json` | 否 | llm stage | 低 | 否 | brief 缓存文件。 |
| `llm_brief_max_papers` | int | `250` | 否 | llm stage | 中 | 否 | 单次 LLM 分析上限。 |
| `llm_brief_timeout_seconds` | int | `20` | 否 | llm stage | 中 | 否 | 单篇 LLM 超时。 |
| `llm_brief_workers` | int | `4` | 否 | llm stage | 中 | 否 | LLM 并发 worker。 |
| `llm_score_threshold` | int | `60` | 否 | llm stage | 中 | 否 | LLM `score` 达标判定 relevant。 |

### 4.9 周报 LLM 聚合

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `weekly_llm_enabled` | bool | `true` | 否 | weekly synthesis | 中 | 否 | 启用周报聚合。 |
| `weekly_llm_model` | string | `qwen-long` | 否 | weekly synthesis | 中 | 否 | 周报聚合模型。 |
| `weekly_llm_timeout_seconds` | int | `25` | 否 | weekly synthesis | 中 | 否 | 周报聚合超时。 |
| `weekly_llm_cache` | string(path) | `storage/data/weekly_llm_cache.json` | 否 | weekly synthesis | 低 | 否 | 周报聚合缓存。 |

### 4.10 推送速率与重试

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `slack_send_interval_seconds` | float | `1.1` | 否 | daily push | 中 | 否 | 两条 webhook 消息间隔。 |
| `slack_max_retries` | int | `4` | 否 | daily + weekly push | 中 | 否 | 单条消息最大重试次数。 |

### 4.11 历史兼容字段

| Key | 类型 | 默认值 | 必填 | 生效范围 | 风险 | 已弃用 | 说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `slack_push_workers` | int | `4` | 否 | 无（当前代码未读取） | 低 | 是 | 历史字段，保留不生效。 |
| `slack_preserve_order` | bool | `true` | 否 | 无（当前代码未读取） | 低 | 是 | 历史字段，当前固定顺序发送。 |

## 约束

- 配置变更必须同时更新本文件与 `config.example.json`。
- 新增 key 未补齐本文件视为不合格提交。
- 不支持在运行中热加载配置，需重启生效。

## 示例

### 最小可运行配置（日报 + 命令）

```json
{
  "slack_webhook_url": "https://hooks.slack.com/services/xxx/yyy/zzz",
  "slack_bot_token": "xoxb-...",
  "slack_app_token": "xapp-...",
  "slack_channel_id": "C0123456789",
  "rss_categories": ["cs.LG", "cs.AI"],
  "daily_report_time_bjt": "09:00"
}
```

## 验收

- 本文件覆盖 `config.example.json` 全部 52 个 key。
- 每个 key 都有类型/默认值/生效范围/弃用标记。
- 运维与开发无需翻代码即可完成配置变更评估。
