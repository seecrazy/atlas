# Roadmap

## 已完成

### Phase 0：设计收敛

- [x] 明确垂直切片范围。
- [x] 明确非目标：不做运行时进化、不做完整四层 Agent。
- [x] 建立 SQLite schema。
- [x] 定义 MCP 工具面。
- [x] 定义 scope token 服务端校验模型。

### Phase 1：知识库垂直切片

- [x] CLI：`init`、`index`、`health`、`serve`。
- [x] SQLite WAL 数据库初始化。
- [x] Python AST 索引。
- [x] TypeScript/JavaScript 基础索引。
- [x] 文件、符号、引用、记忆表结构。
- [x] 记忆漂移标记。
- [x] MCP stdio Server。
- [x] scope token 生成和范围过滤。
- [x] 审计事件。
- [x] pytest 测试覆盖。

## 进行中

### Phase 2：索引可靠性

- [x] Go / Java 基础解析器。
- [x] git diff-tree 增量索引。
- [x] 索引失败报告和文件级诊断。
- [x] 输出 schema 版本化。
- [x] 跨仓引用去重与置信度。
- [x] Rust 解析器。
- [x] 未提交工作区变更检测。
- [x] 健康度 Markdown / JSON 报告。
- [x] 20 条冻结 Router 验证集。
- [x] Router accuracy 指标。
- [x] 导出符号变更传播报告。
- [x] 端到端确定性编排命令。
- [x] 前台增量守护循环。
- [x] Kernel Budget / approvals / verifier。
- [x] 四层 Agent scoped 会话。
- [x] 语义记忆生成与自动复核。
- [x] 架构契约与 ADR 索引。
- [x] 审批补丁工作流。
- [x] 离线进化评分。
- [x] HTTP JSON-RPC Server。
- [x] 并行文件解析与 call edge 基础抽取。
- [x] tree-sitter deep parser 后端。
- [x] SQLite FTS5 hybrid search。
- [x] OS event watchfiles daemon。
- [x] Git / PR 发布工作流。
- [x] semantic / agent / router evaluation harness。
- [x] strategy variant promotion / rollback。

### Phase 3：记忆复核第一闭环

- [x] stale 记忆查询接口。
- [x] 外部审查者提交复核决策。
- [x] 复核记录与审计。

## 未完成

### Phase 3：语义与记忆

- [x] 文件 / repo / subsystem 语义摘要。
- [x] 摘要证据引用复核。
- [x] utility score。
- [x] Router 冻结验证集。
- [x] ATLAS 自项目语义摘要质量评估。
- [ ] 大规模多语言项目语义摘要质量评估。

### Phase 4：角色编排原型

- [x] Router 请求分类 schema。
- [x] 确定性 Router 原型。
- [x] 系统工程师层查询模板。
- [x] 模块工程师层上下文包构建。
- [x] 影响传播链输出。
- [x] 上行回溯汇聚格式。
- [x] 端到端确定性 Pipeline。
- [x] 开发子任务 scoped 会话。
- [x] ATLAS 自项目确定性 Agent 评估。
- [ ] LLM Worker 真实项目评估。

### Phase 5：增量守护

- [x] 前台轮询守护循环。
- [x] commit / diff 事件提取。
- [x] 导出符号变更传播。
- [x] stale 记忆自动批量复核。
- [x] OS event 文件监听。

### Phase 6：受限进化

- [x] 离线变体评分。
- [x] 冻结 Router 评估集。
- [x] 低/中/高三级修改授权。
- [x] 审批补丁工作流。
- [x] 真实项目变体评估 harness。
- [x] 变体提升与回滚自动化。

