# E2E Report: <Card ID>

- Card ID: `FC-YYYYMMDD-NN`
- Test Mode: Simulated
- Executor:
- Executed At:

## 背景

<该 E2E 报告验证的目标和边界>

## 决策

### 用例清单

| Case ID | 类型 | 前置条件 | 模拟依赖 | 结论 |
| --- | --- | --- | --- | --- |
| E2E-001 | 主路径 |  |  | PASS/FAIL/BLOCKED |
| E2E-002 | 失败重试 |  |  | PASS/FAIL/BLOCKED |
| E2E-003 | 幂等重复触发 |  |  | PASS/FAIL/BLOCKED |

### 用例详情

#### E2E-001

- 输入：
- 预期：
- 实际：
- 证据（命令/日志/截图）：
- 结论：PASS | FAIL | BLOCKED

#### E2E-002

- 输入：
- 预期：
- 实际：
- 证据（命令/日志/截图）：
- 结论：PASS | FAIL | BLOCKED

#### E2E-003

- 输入：
- 预期：
- 实际：
- 证据（命令/日志/截图）：
- 结论：PASS | FAIL | BLOCKED

## 约束

- 结论必须使用 `PASS/FAIL/BLOCKED`。
- BLOCKED 必须附带阻塞原因与解除条件。
- 至少覆盖：主路径 + 失败重试 + 幂等路径。

## 示例

- 命令示例：`python3 -m unittest -v test_bug_fixes.py`
- 证据示例：`logs/app.log` 关键行摘录

## 验收

- 本报告足以支撑 G4 门禁判断。
