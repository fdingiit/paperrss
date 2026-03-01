# arXiv RSS 常驻小助手（LLM 训练/推理/基础设施）

这是一个常驻应用（daemon），不是一次性脚本。

它会同时做三件事：
- 按北京时间固定时刻发送日报/周报到 Slack
- 监听 Slack `app_mention` 命令（Socket Mode，`@bot -ping`）
- 提供本地 health check 接口 `GET /healthz`

Slack 推送使用 Block Kit 富文本卡片（标题、字段、按钮）。

## 启动

```bash
cd /Users/fd/Lab/paperrss
python3 app_daemon.py --config storage/config.json --log-level INFO --log-file logs/app.log
```

启动后会打印 health 地址，例如：
- `http://127.0.0.1:8080/healthz`

## 配置

先复制模板：

```bash
mkdir -p /Users/fd/Lab/paperrss/storage
cp /Users/fd/Lab/paperrss/config.example.json /Users/fd/Lab/paperrss/storage/config.json
```

关键配置项：
- `slack_webhook_url`：RSS 结果推送 webhook
- `slack_when`：`any` 或 `relevant`
- `slack_send_interval_seconds`：推送间隔秒数（建议 `>=1`，避免限流）
- `slack_max_retries`：单条消息失败重试次数
- `slack_preserve_order`：是否按 ranking 顺序推送（默认 `true`，当前实现按序串行推送）
- `sort_priority`：排序策略，默认 `inference_acceleration`（最高优先级：推理加速）
- `classify_workers`：分类阶段并发 worker 数（默认 `8`）
- `subscription_store`：订阅历史持久化（已见论文 ID，默认 `storage/data/subscriptions.json`）
- `push_state`：推送去重持久化（已推论文/已推报告日期，默认 `storage/data/push_state.json`）
- `push_state_retention_days`：推送去重保留天数（默认 `14`，按日期分桶自动裁剪）
- `schedule_state`：日报/周报调度状态持久化，默认 `storage/data/schedule_state.json`
- `report_modes`：启用哪些模式，支持 `daily` / `weekly` / `["daily","weekly"]`
- `daily_report_time_bjt`：日报发送时刻，默认北京时间 `09:00`
- `weekly_report_time_bjt`：周报发送时刻，默认北京时间周日 `18:00`
- `author_enrich`：是否从 arXiv HTML 抓作者邮箱，默认 `true`
- `author_cache`：作者线索缓存文件，默认 `storage/data/author_cache.json`
- `author_enrich_max_papers`：单次最多增强多少篇（默认 `60`，避免阻塞推送）
- `author_enrich_timeout_seconds`：单篇作者抓取超时秒数（默认 `8`）
- `author_enrich_workers`：作者增强并发 worker 数（默认 `8`）
- `interest_topics`：订阅兴趣点列表，Qwen 会按这个列表打标签并做统一 `score`
- `llm_brief_enabled`：是否启用 Qwen brief 分析
- `llm_brief_api_key`：阿里百炼 / Qwen API Key
- `llm_brief_base_url`：默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- `llm_brief_model`：默认 `qwen-long`
- `llm_brief_cache`：brief 缓存文件，默认 `storage/data/llm_brief_cache.json`
- `llm_brief_max_papers`：单次最多分析多少篇，默认 `250`
- `llm_brief_timeout_seconds`：单篇分析超时秒数，默认 `20`
- `llm_brief_workers`：brief 分析并发 worker 数，默认 `4`
- `llm_score_threshold`：Qwen `score` 达到该值时会标记为 relevant，默认 `60`
- `weekly_llm_enabled`：是否启用周报 Qwen 聚合总结，默认跟随 `llm_brief_enabled`
- `weekly_llm_model`：周报聚合使用的模型，默认复用 `qwen-long`
- `weekly_llm_timeout_seconds`：周报聚合超时秒数，默认 `25`
- `weekly_llm_cache`：周报聚合缓存，默认 `storage/data/weekly_llm_cache.json`
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
- `server_now_utc / server_now_bjt / server_now_local / server_local_tz / scheduler_timezone`
- `rss.last_run_at / next_run_at / last_status / last_error`
- `weekly.last_run_at / next_run_at / last_status / last_error`
- `pingpong.last_poll_at / last_reply_at / last_status / last_error`

时钟说明：
- 调度始终按 `Asia/Shanghai` 计算，不依赖宿主机当前时区。
- 如果宿主机“日期/时间本身”错了，应用也会跟着错；这必须在宿主机或 NAS 上开启 NTP 自动校时。
- `-ping` 和 `/healthz` 会同时显示服务器当前 `UTC / BJT / Host Local` 时间，方便定位是“时区错”还是“系统时间错”。

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

存储目录建议：
- 统一使用 `storage/` 作为持久化根目录
- `storage/data/`：状态与缓存
- `storage/reports/`：日报文件
- Docker/NAS 只需挂载一个 volume 到 `/app/storage`

Docker / NAS 时间建议：
- 宿主机开启 NTP 自动校时，例如 `ntp.aliyun.com` 或 `pool.ntp.org`
- 启动容器时挂载宿主机时区文件：

```bash
docker run -d \
  --name paperrss \
  -v /path/to/paperrss-storage:/app/storage \
  -v /etc/localtime:/etc/localtime:ro \
  -v /etc/timezone:/etc/timezone:ro \
  paperrss:latest
```

- 如果宿主机没有 `/etc/timezone`，至少挂载 `/etc/localtime`

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

Qwen brief 说明：
- 走阿里百炼兼容接口，不依赖额外 SDK。
- 当前分析输入是 `title + abstract + 规则分类结果`，不是 PDF 全文。
- brief 会缓存到 `storage/data/llm_brief_cache.json`，避免重复计费。
- Qwen 同时输出 `tags / score / interest_matches`。
- 最终排序和展示只使用一个统一 `Score`，依据是用户配置的兴趣点/关键词，由 Qwen 直接匹配打分。
- 周报会额外调用一次 Qwen 聚合本周 top papers，产出 `Engineering Takeaways`。
- 周报聚合结果会缓存到 `storage/data/weekly_llm_cache.json`。

调度说明：
- 日报：默认每天北京时间 `09:00`
- 周报：默认每周日北京时间 `18:00`
- 周报内容来自最近 7 天日报聚合，输出文件名形如 `weekly-YYYY-MM-DD.md`
- 日报格式：先给 `Top Picks`，再给 `Full Ranked Papers`，用于当天阅读决策
- 周报格式：先给 `Weekly Overview / Theme Summary`，再给 `Best Of Week`，用于看趋势而不是逐篇重读

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
