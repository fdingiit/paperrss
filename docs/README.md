# paperrss 文档索引

## 背景

项目从单 README 模式切换为 `README + docs/` 模式，目标是让 AI 代理与工程师都能直接读取结构化规范并执行实现。

## 决策

文档采用固定 6 件套 + 流程工件体系，按职责分层：
- `01-requirements.md`：需求与验收（FR/NFR/AC）
- `02-architecture-design.md`：系统边界与关键架构决策
- `03-system-design.md`：时序、状态模型、幂等与错误处理
- `04-config-reference.md`：配置权威字典（52 个 key）
- `05-operations-runbook.md`：运行、部署、排障、恢复
- `06-adr-log.md`：架构决策记录索引
- `process-vibe-coding.md`：需求到验收的执行流程规范（G1~G6）
- `cards/`、`e2e/`、`acceptance/`、`templates/`：流程工件目录
- `changelog.md`：功能卡片级变更流水
- Demo：`FC-20260313-02`（`-status` 新功能流程样例）

## 约束

- 文档以当前 `main` 代码行为为准。
- 需求、架构、系统设计中的条目都带编号，可被 AI 代理直接引用。
- 配置定义以 `04-config-reference.md` 为唯一权威说明。

## 示例

推荐阅读路径：

1. 新成员（先能跑）
- `README.md` → `05-operations-runbook.md` → `04-config-reference.md`

2. AI 代理（先能改）
- `01-requirements.md` → `02-architecture-design.md` → `03-system-design.md` → `06-adr-log.md`

3. AI 代理（先执行流程）
- `process-vibe-coding.md` → `templates/card-template.md` → `templates/e2e-template.md` → `templates/acceptance-template.md`

4. 运维值班（先能稳）
- `05-operations-runbook.md` → `03-system-design.md`（错误/幂等章节） → `04-config-reference.md`

## 验收

- 6 份主文档与流程文档可独立阅读，无需翻源码才能理解关键决策。
- README 保持轻量入口，不再承载全部设计细节。
- 任意一个需求条目都可追溯到架构与系统实现约束。
