# Vibe Coding 流程规范（本地文档驱动）

## 背景

为了让需求讨论、实现、测试、验收和文档沉淀形成可追溯闭环，paperrss 采用“功能卡片（Card）+ 门禁（Gate）”流程。流程主体不依赖 GitHub CI，默认以本地文档作为唯一执行与审计载体。

## 决策

### 1) 流程单元与命名

- 流程单元：功能卡片（Feature Card）
- `Card ID` 规则：`FC-YYYYMMDD-NN`（示例：`FC-20260313-01`）
- 一个 Card 只能对应一个清晰目标，不跨多个独立功能

### 2) 工件与目录（固定）

| 工件 | 路径 | 责任 |
| --- | --- | --- |
| Card | `docs/cards/<Card ID>.md` | 需求、方案、任务、DoD |
| E2E 报告 | `docs/e2e/<Card ID>.md` | 模拟 E2E 用例与证据 |
| 验收记录 | `docs/acceptance/<Card ID>.md` | 人工签收结果 |
| 流程总规范 | `docs/process-vibe-coding.md` | 门禁定义与执行规则 |
| 模板 | `docs/templates/*.md` | 标准字段模板 |
| 变更流水 | `docs/changelog.md` | 功能级持续记录 |

### 3) 门禁定义（G1-G6）

| Gate | 阶段 | 输入 | 输出 | 通过条件 |
| --- | --- | --- | --- | --- |
| G1 | 需求讨论 | 想法/问题陈述 | Card 初稿 | FR/NFR/AC 可测量；非目标明确 |
| G2 | 方案冻结 | Card 初稿 | 完整设计卡片 | 每条 FR 有实现点 + 测试点 |
| G3 | 实现与单测 | 冻结卡片 | 代码 + 单测结果 | 单测通过，DoD 全勾选 |
| G4 | 模拟 E2E | 已实现功能 | E2E 报告 | 主路径/失败重试/幂等重复触发至少 3 类用例 |
| G5 | 人工验收 | Card + E2E | 验收记录 | 验收人将结论签为 `Accepted` |
| G6 | 文档持久化 | 验收结果 | changelog + (必要时 ADR) | Card/E2E/Acceptance/Changelog 可追溯 |

### 4) 默认策略

- 测试策略：默认模拟 E2E（mock/stub），不调用真实 Slack/Qwen/arXiv
- 验收裁决：由项目 owner 人工签收
- 变更治理：若涉及架构语义、状态 schema、配置语义，必须追加 ADR

## 约束

- 每个 `Card ID` 必须在 `cards`、`e2e`、`acceptance`、`changelog` 四处都可检索到。
- 验收结论是 `Rejected` 时，不允许进入 G6；必须回退到 G2 或 G3，更新同一 `Card ID` 的版本记录并重测。
- 文档证据必须可复现，禁止仅写“已测试通过”而无命令/结果摘要。

## 示例

### 新建一个 Card 的最小步骤

1. 复制模板并创建卡片

```bash
cp docs/templates/card-template.md docs/cards/FC-20260313-02.md
```

2. 创建 E2E 与验收文档

```bash
cp docs/templates/e2e-template.md docs/e2e/FC-20260313-02.md
cp docs/templates/acceptance-template.md docs/acceptance/FC-20260313-02.md
```

3. 开发完成后填写证据并更新 `docs/changelog.md`

### 快速检索一致性

```bash
rg -n "FC-20260313-02" docs/cards docs/e2e docs/acceptance docs/changelog.md
```

### 现成 demo 参考

- Demo Card：`docs/cards/FC-20260313-02.md`
- Demo E2E：`docs/e2e/FC-20260313-02.md`
- Demo Acceptance：`docs/acceptance/FC-20260313-02.md`

## 验收

- 任何功能都能从一个 `Card ID` 回溯到需求、测试、验收与变更记录。
- 新同学可只看本规范 + 模板就启动完整流程。
- 文档链路可支持后续自动化（例如 CI 校验 G3/G4）。
