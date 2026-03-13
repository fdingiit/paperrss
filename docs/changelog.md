# Changelog（Feature Card Level）

## 背景

本文件记录按功能卡片粒度的变更流水，用于把 `Card -> E2E -> Acceptance -> Docs` 串成可审计链路。

## 决策

- 每条记录必须包含：日期、Card ID、摘要、结论、风险、回滚状态。
- 结论只允许：`Accepted`、`Rejected`。

## 记录

| Date | Card ID | Summary | Decision | Risk | Rollback | Links |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-03-13 | `FC-20260313-02` | 新功能 demo：`-status` 轻量状态命令（流程样例，doc-only） | Accepted | Low | N/A | `cards/FC-20260313-02.md` / `e2e/FC-20260313-02.md` / `acceptance/FC-20260313-02.md` |
| 2026-03-13 | `FC-20260313-01` | 初始化本地文档驱动 vibe coding 流程（目录+模板+门禁） | Accepted | Low | N/A | `cards/FC-20260313-01.md` / `e2e/FC-20260313-01.md` / `acceptance/FC-20260313-01.md` |

## 约束

- Card ID 必须唯一。
- Rejected 记录必须保留，不允许覆盖历史。

## 示例

- 新增条目时，按时间倒序插入到“记录”表顶部。

## 验收

- 任意 Card ID 在本表都能回链到对应工件。
