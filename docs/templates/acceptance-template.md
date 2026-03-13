# Acceptance Record: <Card ID>

- Card ID: `FC-YYYYMMDD-NN`
- Reviewer (Signer):
- Reviewed At:
- Final Decision: Accepted | Rejected

## 背景

<本次验收范围与输入工件>

## 决策

### 输入工件

- Card: `docs/cards/<Card ID>.md`
- E2E: `docs/e2e/<Card ID>.md`
- 代码与测试证据：

### AC 对照检查

| AC 编号 | 标准 | 证据 | 结果 |
| --- | --- | --- | --- |
| AC-001 |  |  | PASS/FAIL |

### 签收结论

- Decision: Accepted | Rejected
- 说明：

## 约束

- Rejected 时必须注明阻塞项和重测入口（返回 G2 或 G3）。
- 不能只写“通过”，必须有 AC 对照证据。

## 示例

- 通过示例：`AC-001~AC-004` 全部 PASS，Decision=Accepted。
- 拒绝示例：`AC-003` 失败，Decision=Rejected，要求补测后重签。

## 验收

- 该记录可独立作为 G5 的签收依据。
