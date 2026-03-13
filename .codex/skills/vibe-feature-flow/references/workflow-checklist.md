# 流程检查清单（G1-G6）

## G1 需求讨论
- 创建 `docs/cards/<Card ID>.md`
- 补齐目标/非目标
- 定义可测量 FR/NFR/AC

## G2 方案冻结
- 填写影响面（代码/配置/状态/外部接口）
- 填写风险与回滚
- 确认每个 FR 对应实现点与测试点

## G3 实现与单测
- 完成代码改动
- 运行单测并在 Card 记录关键信息

## G4 模拟 E2E
- 更新 `docs/e2e/<Card ID>.md`
- 至少覆盖三类用例：主路径、失败重试、幂等

## G5 人工验收
- 更新 `docs/acceptance/<Card ID>.md`
- 按 AC 逐条签收（Accepted/Rejected）

## G6 文档持久化
- 更新 `docs/changelog.md`
- 若涉及配置/状态/架构语义，回写 `01~06` 并新增 ADR
- 用 `rg` 确认 Card ID 在 cards/e2e/acceptance/changelog 全部命中
