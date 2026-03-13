# 06 ADR 记录索引（Architecture Decision Record Log）

## 背景

项目后续会持续演进推送策略、排序逻辑、LLM 依赖与状态模型。为避免“决策散落在 PR 评论里不可追溯”，需要集中 ADR 记录。

## 决策

- ADR-LOG-001：所有影响系统边界/接口/语义的改动必须有 ADR。
- ADR-LOG-002：ADR 文件统一放在 `docs/adr/`。
- ADR-LOG-003：ADR 标题采用 `ADR-XXXX` 递增编号。

### ADR 索引

| 编号 | 标题 | 状态 | 影响文档 | 文件 |
| --- | --- | --- | --- | --- |
| ADR-0001 | ADR 模板（起始条目） | Accepted | 01/02/03/04/05 | [`docs/adr/ADR-0001.md`](adr/ADR-0001.md) |

### 需要持续补充的决策主题

- 排序与打分策略（`sort_priority`、`llm_score_threshold`）
- 推送策略（摘要模式、重试、去重窗口）
- 周报聚合窗口与模板
- 状态文件 schema 变更
- 外部依赖策略（Slack/Qwen/arXiv 的退化路径）

## 约束

- ADR 必须包含：背景、决策、影响、备选方案、回滚策略。
- ADR 变更必须在 PR 描述中显式引用编号。
- 如果变更了配置/状态字段，ADR 必须同步链接到 `03` 与 `04` 的受影响章节。

## 示例

### 新增 ADR 的最小流程

1. 复制 `docs/adr/ADR-0001.md` 模板为新编号文件。
2. 填写 `Status`（Proposed/Accepted/Superseded）。
3. 在本文索引表新增一行。
4. 在受影响文档（01~05）加回链。

## 验收

- 索引表与 `docs/adr/` 文件列表一致。
- 关键架构决策都能从 PR 回溯到 ADR 文档。
- 新增成员可在 10 分钟内找到“为什么这么设计”的依据。
