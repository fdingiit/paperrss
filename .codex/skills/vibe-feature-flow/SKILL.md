---
name: vibe-feature-flow
description: 为仓库建立并维护基于 docs 的功能卡片工作流。用户提出“开始/继续一个 vibe coding 功能迭代”“创建演示功能包”“生成 Card/E2E/Acceptance 工件”“按 G1-G6 门禁推进”“沉淀可追溯文档到 docs/cards、docs/e2e、docs/acceptance、docs/changelog”时使用。
allow_implicit_invocation: true
---

# Vibe 功能卡流程

## 概览

使用本技能将功能交付按“文档先行 + Card ID 可追溯 + G1-G6 门禁”执行。

## 执行流程

1. 先确认仓库有以下路径：
- `docs/templates/card-template.md`
- `docs/templates/e2e-template.md`
- `docs/templates/acceptance-template.md`
- `docs/changelog.md`

2. 为新 Card ID 生成三联工件：

```bash
python3 .claude/skills/vibe-feature-flow/scripts/new_card_pack.py \
  --repo-root . \
  --card-id FC-YYYYMMDD-NN \
  --title "<功能标题>" \
  --owner "<负责人>" \
  --reviewer "<验收人>" \
  --add-changelog \
  --changelog-summary "<功能摘要>"
```

3. 按顺序补齐内容：
- `docs/cards/<Card ID>.md`：G1/G2/G3（需求、方案、DoD）
- `docs/e2e/<Card ID>.md`：G4 证据
- `docs/acceptance/<Card ID>.md`：G5 人工签收
- `docs/changelog.md`：G6 持久化

4. 运行可追溯检查：

```bash
rg -n "<Card ID>" docs/cards docs/e2e docs/acceptance docs/changelog.md
```

5. 若功能改动了系统语义（配置/状态/架构），同步更新基础文档与 ADR：
- `docs/03-system-design.md`
- `docs/04-config-reference.md`
- `docs/06-adr-log.md` and `docs/adr/ADR-XXXX.md`

## 门禁标准

- G1 通过：FR/NFR/AC 可测量，非目标明确。
- G2 通过：每个 FR 都有实现点和测试点映射。
- G3 通过：实现完成，单测结果记录进 Card。
- G4 通过：主路径 + 失败重试 + 幂等路径都有 E2E 证据。
- G5 通过：人工验收人按 AC 给出 `Accepted/Rejected` 结论。
- G6 通过：changelog 与跨文档更新完成，且可追溯。

## 参考资料

端到端检查时读取：
- `references/workflow-checklist.md`

## 资源

### scripts/
- `scripts/new_card_pack.py`：从模板生成 Card/E2E/Acceptance 三联文件，并可追加 changelog 记录。

### references/
- `references/workflow-checklist.md`：G1-G6 执行检查清单。
