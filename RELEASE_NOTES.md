# Release Notes

## v0.4.0 (2026-03-13)

### 1. 调度与推送稳定性
- 修复日报在失败场景下被误标记为“今日已执行”的问题；失败后按 `daily_retry_seconds` 重试。
- 每次日报触发时，先向 Slack 推送一条“RSS Daily Run Started”开始通知。
- 增强推送状态持久化，消息级增量写入，降低中途失败后重复/漏推风险。

### 2. 超过 24 小时窗口的分日报推送
- 保持 `last_run` 增量窗口逻辑不变。
- 当 `now - last_run > 1 day` 时，不再合并为一份超长日报，而是按天拆分成多份日报并逐天推送。
- 按天维度做推送去重与状态记录，支持回填窗口场景。

### 3. Slack 日报消息结构优化
- 新默认结构：
  - 1 条 brief 总览
  - Top 10 单条 detail
  - 剩余论文合并为 1 条 compact thread-style 消息
- 尾部合并消息排版优化：每条“标题行 + 摘要行”，条目间空行分隔，提升可读性。

### 4. Qwen 中文输出与兜底强化
- 强化 Qwen brief 中文约束，确保 brief/tags/interest 输出中文。
- 当模型返回不符合预期时，自动进行中文兜底，避免英文/空内容进入日报。

### 5. 作者组织分析升级（Qwen 驱动）
- 作者增强链路从“仅邮箱提取”升级为“组织识别”双阶段：
  - 阶段 A：规则候选（affiliation 字段 + 邮箱域名）
  - 阶段 B：Qwen 归一化组织识别（输出组织名与分析说明）
- 在日报与 Slack detail 中展示 `Organizations` 字段与组织分析说明。
- 默认复用 `llm_brief_*` 配置，也支持独立配置：
  - `author_org_llm_enabled`
  - `author_org_llm_api_key`
  - `author_org_llm_base_url`
  - `author_org_llm_model`
  - `author_org_llm_timeout_seconds`
  - `author_org_llm_workers`

### 6. 测试覆盖
新增并通过以下测试：
- `tests/test_daily_scheduler.py`
- `tests/test_qwen_language.py`
- `tests/test_report_buckets.py`
- `tests/test_slack_layout.py`
- `tests/test_author_organizations.py`

### 7. 文档更新
- 重写 README，补齐启动、配置、调度、Qwen 与 Socket Mode 使用说明。
