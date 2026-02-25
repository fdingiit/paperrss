# arXiv RSS 常驻小助手（LLM 训练/推理/基础设施）

这是一个常驻应用（daemon），不是一次性脚本。

它会同时做三件事：
- 定时增量抓取 arXiv 并推送 Slack
- 监听 Slack `app_mention` 命令（Socket Mode，`@bot -ping`）
- 提供本地 health check 接口 `GET /healthz`

Slack 推送使用 Block Kit 富文本卡片（标题、字段、按钮）。

## 启动

```bash
cd /Users/fd/Lab/paperrss
python3 app_daemon.py --config config.json --log-level INFO --log-file logs/app.log
```

启动后会打印 health 地址，例如：
- `http://127.0.0.1:8080/healthz`

## 配置

先复制模板：

```bash
cp /Users/fd/Lab/paperrss/config.example.json /Users/fd/Lab/paperrss/config.json
```

关键配置项：
- `slack_webhook_url`：RSS 结果推送 webhook
- `slack_when`：`any` 或 `relevant`
- `slack_send_interval_seconds`：推送间隔秒数（建议 `>=1`，避免限流）
- `slack_max_retries`：单条消息失败重试次数
- `slack_preserve_order`：是否按 ranking 顺序推送（默认 `true`，当前实现按序串行推送）
- `sort_priority`：排序策略，默认 `inference_acceleration`（最高优先级：推理加速）
- `classify_workers`：分类阶段并发 worker 数（默认 `8`）
- `author_enrich`：是否从 arXiv HTML 抓作者邮箱，默认 `true`
- `author_cache`：作者线索缓存文件，默认 `data/author_cache.json`
- `author_enrich_max_papers`：单次最多增强多少篇（默认 `60`，避免阻塞推送）
- `author_enrich_timeout_seconds`：单篇作者抓取超时秒数（默认 `8`）
- `author_enrich_workers`：作者增强并发 worker 数（默认 `8`）
- `rss_interval_seconds`：RSS 定时间隔，默认 `86400`（每天）
- `slack_bot_token`：用于 ping/pong 的 Bot Token（`xoxb-...`）
- `slack_app_token`：Socket Mode App-Level Token（`xapp-...`）
- `slack_channel_id`：监听并回复的频道 ID（`C...`）
- `cmd_reply_in_thread`：是否在线程内回复（默认 `true`）
- `health_host` / `health_port`：health server 地址，默认 `127.0.0.1:8080`
- `log_level` / `log_file`：日志级别与日志文件（daemon 会同时输出到终端和文件）

## Health Check

```bash
curl -s http://127.0.0.1:8080/healthz
```

返回 JSON，含：
- `rss.last_run_at / last_status / last_error`
- `pingpong.last_poll_at / last_reply_at / last_status / last_error`

## Slack 命令权限

Slack App 需要这些 scope：
- `chat:write`
- `app_mentions:read`
- App-Level Token scope: `connections:write`

并重新安装 App 到 workspace，然后把 Bot 邀请进目标频道。

## 命令协议

在频道里 `@bot` 后跟命令：
- `@bot -ping`：返回 `pong` 和 `/healthz` 状态摘要
- `@bot -help`：列出支持命令
- `@bot -force`：清空当日 state/report 并立即全量重跑当日任务

默认在 thread 内回复，避免刷屏。

## 设计说明（toolkit）

- 接入层：Socket Mode WebSocket（`apps.connections.open`）
- 事件层：接收 `events_api`，过滤 `app_mention`
- ACK：每个 envelope 先返回 `{"envelope_id": ...}`
- 路由层：`-ping`、`-help` 映射到 handler，后续可扩展更多命令
- 执行层：调用 Slack `chat.postMessage` 回复（支持 thread 回复）
- 幂等：按 `event_id` 做本地去重，避免重连重复回复

作者信息说明：
- 会优先抓取 arXiv HTML（experimental）并抽取邮箱地址。
- 这是启发式抽取，不保证每篇都有可用邮箱。

并发设计说明：
- 分类阶段并发（`classify_workers`）提高吞吐。
- 作者增强阶段并发（`author_enrich_workers`）并带缓存。
- 报告生成与 Slack 推送保持 rank 顺序，避免乱序阅读。
- 状态文件在推送阶段之后再更新，保证流程完整闭环。

## 手动单次执行（保留）

如果你只想单次跑 RSS：

```bash
python3 arxiv_rss_assistant.py --force-push --sort-priority inference_acceleration --log-level INFO --log-file logs/rss.log
```

如果你只想先确保 Slack 立即推送（不做作者增强）：

```bash
python3 arxiv_rss_assistant.py --force-push --no-author-enrich
```
