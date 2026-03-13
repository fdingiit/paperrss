# PaperRSS

面向 LLM 训练/推理/基础设施方向的 arXiv 常驻订阅服务。

它会持续运行并完成三件事：
- 按北京时间定时生成日报、周报
- 将结果通过 Slack 推送（Block Kit 卡片）
- 提供 Slack `@bot` 命令与本地健康检查接口

## 核心能力

- 增量订阅：基于本地状态去重，只处理新增论文
- 打分排序：按训练/推理/基础设施分类并排序（默认偏重推理加速）
- Slack 推送：日报推送前会先发送一条“RSS 开始执行”通知
- 调度容错：日报失败时不会错误标记为“今日已完成”，会按重试间隔继续尝试
- 周报聚合：从最近 7 天日报聚合主题，可选 Qwen 总结
- 运维可观测：`/healthz` 暴露调度与命令通道状态

## 代码结构

- `app_daemon.py`：主进程（日报/周报调度 + Socket Mode 命令 + health server）
- `arxiv_rss_assistant.py`：单次 RSS 执行管线（抓取、分类、报告、Slack 推送）
- `slack_cmd_toolkit.py`：命令路由与回复构造（`-ping/-brief/-help`）
- `slack_healthcheck.py`：Slack API 轻量封装与独立 healthcheck 工具
- `config.example.json`：配置模板

## 环境要求

- Python `3.10+`（推荐 `3.11`）
- 若启用 Slack Socket Mode（`@bot -ping/-force`），需安装：

```bash
pip install websocket-client
```

> 仅使用 webhook 推送（不使用 Socket Mode 命令）时，不安装 `websocket-client` 也可运行。

## 快速开始

1. 准备配置文件

```bash
mkdir -p storage
cp config.example.json storage/config.json
```

2. 修改最小可用配置（其余可先保持默认）

```json
{
  "slack_webhook_url": "https://hooks.slack.com/services/xxx/yyy/zzz",
  "report_modes": ["daily"],
  "daily_report_time_bjt": "09:00",
  "rss_categories": ["cs.LG", "cs.AI", "cs.CL", "cs.DC", "stat.ML"],
  "health_host": "127.0.0.1",
  "health_port": 8080,
  "llm_brief_enabled": false,
  "weekly_llm_enabled": false
}
```

3. 启动

```bash
python3 app_daemon.py --config storage/config.json --log-level INFO --log-file logs/app.log
```

4. 查看健康状态

```bash
curl -s http://127.0.0.1:8080/healthz
```

## 配置说明

### 1) 必配（至少建议配置）

- `slack_webhook_url`：Slack Incoming Webhook（日报、周报、日报开始通知都用它）
- `rss_categories`：arXiv 分类列表
- `report_modes`：`daily` / `weekly` / `["daily","weekly"]`
- `daily_report_time_bjt`：日报触发时间（北京时间）

### 2) 调度相关

- `schedule_state`：调度状态文件（默认 `storage/data/schedule_state.json`）
- `daily_retry_seconds`：日报失败后的重试等待秒数（默认 `300`）
- `weekly_report_time_bjt`：周报触发时间（默认周日 `18:00`）

### 3) RSS/状态存储

- `rss_max_results`：每次抓取上限
- `rss_state`：RSS 增量状态（`last_run`、`seen_ids`）
- `subscription_store`：历史已见 ID
- `push_state`：Slack 推送去重状态
- `push_state_retention_days`：去重保留天数（默认 `14`）
- `rss_output_dir`：日报目录
- `weekly_output_dir`：周报目录
- `rss_feed_file`：本地 Atom XML（离线调试用）

### 4) 推送/排序

- `slack_when`：`any` / `relevant`
- `force_push_date`：当值等于当天 `YYYY-MM-DD` 时，本次日报强制推送
- `slack_send_interval_seconds`：消息间隔秒数
- `slack_max_retries`：单条消息最大重试次数
- `sort_priority`：`inference_acceleration` / `balanced` / `recent`
- `classify_workers`：分类并发数

### 5) 作者增强（可选）

- `author_enrich`：是否抓取作者线索
- `author_cache`：作者缓存文件
- `author_enrich_max_papers`
- `author_enrich_timeout_seconds`
- `author_enrich_workers`

### 6) Qwen（可选）

- `llm_brief_enabled`：启用日报级别 Qwen brief
- `llm_brief_api_key`：Qwen API key（不开启可不配）
- `llm_brief_base_url`：默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- `llm_brief_model`：默认 `qwen-long`
- `llm_brief_cache`
- `llm_brief_max_papers`
- `llm_brief_timeout_seconds`
- `llm_brief_workers`
- `llm_score_threshold`：Qwen 分数阈值（兼容旧键 `llm_recommendation_threshold`）
- `interest_topics`：关注主题词
- `weekly_llm_enabled`：周报聚合是否启用 Qwen（默认跟随 `llm_brief_enabled`）
- `weekly_llm_model`
- `weekly_llm_timeout_seconds`
- `weekly_llm_cache`

### 7) Socket Mode 命令（可选）

只在你需要 `@bot -ping/-brief/-force/-help` 时配置：

- `slack_bot_token`：`xoxb-...`
- `slack_app_token`：`xapp-...`
- `slack_channel_id`：频道 ID（`C...`）
- `cmd_reply_in_thread`：是否在线程内回复（默认 `true`）

### 8) 健康检查与日志

- `health_host` / `health_port`：health server 地址
- `log_level` / `log_file`：日志等级与日志文件

### 9) 兼容字段说明

`config.example.json` 中有少量历史字段（例如 `slack_push_workers`、`slack_preserve_order`），当前主流程不会读取，可保留但不会影响运行。

## Slack 集成说明

### 仅推送（最简）

只配 `slack_webhook_url` 即可。

### 启用 `@bot` 命令

Slack App 需至少有：
- Bot scope: `chat:write`
- Event scope: `app_mentions:read`
- App-level scope: `connections:write`

并将 App 安装到 workspace、把 bot 邀请进目标频道。

## 命令协议

在频道里 `@bot` 后输入：
- `-ping`：返回 `pong` 与 health 摘要
- `-brief`：返回最近日报摘要
- `-force`：清理当日 `state/report` 并立即后台全量重跑
- `-help`：查看命令说明

## 调度与重试语义

- 调度时区固定为 `Asia/Shanghai`
- 日报触发时，如果配置了 webhook，会先发送：`RSS daily run started (YYYY-MM-DD)`
- 日报执行成功（exit code `0`）才会写入 `last_daily_key`
- 日报执行失败不会写 `last_daily_key`，会等待 `daily_retry_seconds` 后再次尝试
- 周报按周日窗口聚合最近 7 天日报

## Health 字段速查

`GET /healthz` 关键字段：
- `server_now_utc / server_now_bjt / server_now_local / server_local_tz / scheduler_timezone`
- `rss.last_run_at / rss.next_run_at / rss.last_status / rss.last_error`
- `weekly.last_run_at / weekly.next_run_at / weekly.last_status / weekly.last_error`
- `pingpong.last_poll_at / pingpong.last_reply_at / pingpong.last_status / pingpong.last_error`

## 单次执行模式（非 daemon）

可直接运行一次 RSS 管线：

```bash
python3 arxiv_rss_assistant.py --config storage/config.json
```

常用参数：

```bash
# 强制推送、关闭作者增强
python3 arxiv_rss_assistant.py --config storage/config.json --force-push --no-author-enrich

# 离线调试：使用本地 feed 文件
python3 arxiv_rss_assistant.py --config storage/config.json --feed-file /path/to/feed.xml
```

## 本地调试建议

- 想立即触发日报：
1. 暂时把 `daily_report_time_bjt` 设成早于当前时间（同一天）
2. 删除 `schedule_state` 中的 `last_daily_key`（或删掉整个文件）
3. 重启 daemon

- 想验证去重/重跑：
1. 查看 `push_state` 和 `rss_state`
2. 使用 `@bot -force` 触发一次全量重跑

## 测试

```bash
python3 -m unittest discover -s tests -p 'test*.py' -q
```

当前已覆盖：
- 日报触发时发送“开始执行”通知
- 日报失败不写 `last_daily_key`
- 日报成功写 `last_daily_key`

## Docker

```bash
docker build -t paperrss:latest .

docker run -d \
  --name paperrss \
  -v /path/to/paperrss-storage:/app/storage \
  -v /etc/localtime:/etc/localtime:ro \
  -v /etc/timezone:/etc/timezone:ro \
  paperrss:latest
```

`Dockerfile` 默认启动命令：

```bash
python app_daemon.py --config storage/config.json --log-level INFO --log-file /app/logs/app.log
```
