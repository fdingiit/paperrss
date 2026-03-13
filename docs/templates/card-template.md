# <Card ID>: <功能标题>

- Card ID: `FC-YYYYMMDD-NN`
- Status: Draft | Frozen | In Progress | Ready for Acceptance | Accepted | Rejected
- Owner:
- Created At:
- Updated At:

## 背景

<为什么要做，当前痛点是什么>

## 决策

### 目标与非目标

- 目标：
- 非目标：

### FR（功能需求）

| 编号 | 需求描述 | 实现点映射 |
| --- | --- | --- |
| FR-001 |  |  |

### NFR（非功能需求）

| 编号 | 需求描述 | 验证方式 |
| --- | --- | --- |
| NFR-001 |  |  |

### AC（验收标准）

| 编号 | 可测量标准 | 对应测试（单测/E2E） |
| --- | --- | --- |
| AC-001 |  |  |

### 影响面

| 维度 | 影响内容 |
| --- | --- |
| 代码模块 |  |
| 配置项 |  |
| 状态文件 |  |
| 外部接口 |  |
| 文档章节 |  |

### 风险与回滚

- 风险：
- 回滚策略：

### 任务分解（Implementation Plan）

| 任务 | 描述 | 状态 |
| --- | --- | --- |
| T-001 |  | TODO |

### DoD（完成定义）

- [ ] 代码实现完成
- [ ] 单测通过（G3）
- [ ] 模拟 E2E 通过（G4）
- [ ] 人工验收签收（G5）
- [ ] 文档与 changelog 更新（G6）

## 约束

- 每条 FR 必须能追溯到具体实现点。
- 每条 AC 必须可被测试结果直接证明。
- 若验收拒绝，必须保留同一 `Card ID` 的迭代记录。

## 示例

- 证据链接示例：
  - 单测输出：`<paste key lines>`
  - E2E 报告：`docs/e2e/<Card ID>.md`
  - 验收记录：`docs/acceptance/<Card ID>.md`

## 验收

- 本卡片字段填写完整，可独立交给实现者执行。
