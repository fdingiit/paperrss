# 05 运行与故障手册（Operations Runbook）

## 背景

paperrss 作为常驻守护进程，运行时风险集中在三类：
- 定时任务未按预期触发
- 外部依赖（Slack/arXiv/Qwen）短时失败
- 状态文件异常导致重复推送或漏推送

本手册用于本地、容器、NAS 场景的标准运行和应急恢复。

## 决策

### O-001 本地启动流程

```bash
cd /Users/fdingiit/Lab/paperrss
mkdir -p storage
cp config.example.json storage/config.json
python3 app_daemon.py --config storage/config.json --log-level INFO --log-file logs/app.log
```

启动后验证：

```bash
curl -s http://127.0.0.1:8080/healthz
```

### O-002 Docker/NAS 部署基线

```bash
docker run -d \
  --name paperrss \
  -v /path/to/paperrss-storage:/app/storage \
  -v /etc/localtime:/etc/localtime:ro \
  -v /etc/timezone:/etc/timezone:ro \
  paperrss:latest
```

建议：
- 宿主机开启 NTP 自动校时。
- 持久化目录至少保留 `storage/data` 与 `storage/reports`。

### O-003 关键观测点

| 观测项 | 检查方式 | 正常信号 |
| --- | --- | --- |
| 进程存活 | `ps` / 容器状态 | daemon 持续运行 |
| 健康接口 | `GET /healthz` | 返回 200 且 JSON 可解析 |
| 日报调度 | `health.rss.next_run_at` 与日志 | 到点后 `last_status=ok` |
| 周报调度 | `health.weekly.next_run_at` 与日志 | 到点后 `last_status=ok` 或 `no_reports_found` |
| 命令通道 | Slack `@bot -ping` | 有响应且状态字段完整 |

## 约束

- 排障优先使用状态文件与日志，不直接手改代码。
- 线上恢复禁止使用破坏性 git 命令（例如 `reset --hard`）。
- 手工删除状态文件前必须明确影响范围（可能触发重复推送）。

## 示例

### E-001 常见故障排查矩阵

| 现象 | 可能原因 | 排查步骤 | 恢复动作 |
| --- | --- | --- | --- |
| 到点没日报 | `report_modes` 不含 `daily`；时间配置错误；进程未运行 | 查 `/healthz` + `logs/app.log` + `schedule_state` | 修配置并重启 daemon |
| 收到 health ping 但无论文推送 | `slack_webhook_url` 缺失/错误；去重后无新论文 | 查 `rss.last_status` + push 日志 + `push_state` | 修 webhook 或用 `-force` 触发 |
| 周报为空 | 最近 7 天无日报文件或解析失败 | 查 `storage/reports/*.md` 与周报日志 | 补日报或修复日报内容格式 |
| 命令无响应 | token 缺失、Socket 连接失败、依赖缺失 | 查 `pingpong.last_error` 与 socket 日志 | 补 token、安装依赖、重启 |
| 重复推送 | `push_state` 损坏或被清空 | 查 `storage/data/push_state.json` | 恢复备份；必要时临时关闭推送 |

### E-002 恢复流程（强制重跑）

1. 在 Slack 发 `@bot -force`。
2. 观察“started”提示。
3. 等待“done”回帖并确认状态。
4. 检查 `storage/reports/YYYY-MM-DD.md` 与推送结果。

### E-003 日常值班检查清单

- C-001：`/healthz` 可用且三类状态非 `error`。
- C-002：`rss.next_run_at` 与 `weekly.next_run_at` 时间合理。
- C-003：日志中无持续重试风暴（Slack/Qwen/arXiv）。
- C-004：`storage/data` 文件可读写且 JSON 合法。
- C-005：配置密钥未过期（Slack/Qwen）。

## 验收

- 新环境按手册可在 10 分钟内启动并完成健康验证。
- 值班人员可仅凭手册完成“日报未触发”与“命令失效”两类故障恢复。
- 手册内容与当前 `main` 的日志字段、状态字段一致。
