# paperrss

arXiv LLM 论文订阅与 Slack 推送守护进程。

本项目包含 4 条长期运行能力：
- 每日定时拉取 + 排序 + 推送日报
- 每周聚合日报并推送周报
- Slack Socket Mode 命令通道（`-ping/-brief/-force/-help`）
- 本地健康检查接口（`/healthz`）

## 3 分钟启动

1. 准备配置文件。

```bash
cd /Users/fdingiit/Lab/paperrss
mkdir -p storage
cp config.example.json storage/config.json
```

2. 至少填写以下配置：
- `slack_webhook_url`（日报/周报推送）
- `slack_bot_token`、`slack_app_token`、`slack_channel_id`（Slack 命令能力）
- `llm_brief_enabled=true` 时补充 `llm_brief_api_key`

3. 启动 daemon。

```bash
python3 app_daemon.py --config storage/config.json --log-level INFO --log-file logs/app.log
```

4. 验证健康状态。

```bash
curl -s http://127.0.0.1:8080/healthz
```

## 文档导航

完整文档已拆分到 `docs/`：
- [文档总索引](docs/README.md)
- [需求文档](docs/01-requirements.md)
- [架构设计文档](docs/02-architecture-design.md)
- [系统设计文档](docs/03-system-design.md)
- [配置字典](docs/04-config-reference.md)
- [运行与故障手册](docs/05-operations-runbook.md)
- [ADR 记录索引](docs/06-adr-log.md)
- [Vibe Coding 流程规范](docs/process-vibe-coding.md)
- [功能级变更流水](docs/changelog.md)

## 常用命令

单次执行 RSS（不走 daemon）：

```bash
python3 arxiv_rss_assistant.py --config storage/config.json --log-level INFO --log-file logs/rss.log
```

强制推送（忽略当日去重）：

```bash
python3 arxiv_rss_assistant.py --config storage/config.json --force-push
```

运行 v0.4.0 回归测试：

```bash
python3 -m unittest -v test_bug_fixes.py
```

## 版本

当前版本见 [`VERSION`](VERSION)。
