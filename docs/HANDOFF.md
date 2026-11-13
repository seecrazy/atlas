# 交接记录

## 最近更新

2026-11-12

## 当前结论

ATLAS 第一版先交付「可验证项目知识库 + MCP」，不先做完整四层 Agent。原因如下：

1. Agent 路由质量依赖底层索引准确率。
2. 没有可观察的符号/引用/影响数据前，难以评估路由是否正确。
3. scope 权限必须在服务端可验证，不能只靠提示词或客户端行为。
4. 语义记忆必须带证据和状态，否则会污染 Agent 上下文。
5. 进化闭环必须离线且有冻结验证集，不能直接进入运行时。

## 已实现

项目位于 `atlas/` 目录。

已实现 Phase 1 与 Phase 2 第一批能力：

1. `.atlas/config.json` 项目配置。
2. `.atlas/index.db` SQLite 数据库。
3. Python AST 抽取。
4. TypeScript/JavaScript 基础抽取。
5. 符号、引用、文件、记忆、scope、audit 表。
6. MCP stdio 工具。
7. scope token 与路径过滤。
8. pytest 测试。
9. Go / Java 基础符号索引。
10. Git-aware 提交间增量索引。
11. 文件级索引诊断。
12. Rust 基础索引。
13. 引用边解析、置信度、跨仓标识和去重。
14. schema 版本 3、旧库列迁移和记忆复核表。
15. 确定性 Router 原型与 `atlas_route`。
16. 影响分析置信度过滤。
17. 记忆检索与复核闭环。
18. 系统 / 模块 / 开发三层结构化上下文包。
19. 两级影响传播链。
20. 上行回溯汇聚格式。
21. 未提交工作区变更检测。
22. Markdown / JSON 健康度报告。
23. 20 条 Router 冻结验证集与 accuracy 指标。
24. 导出符号变更传播报告。
25. Router -> Context -> Worker -> Convergence 端到端命令。
26. 前台增量守护循环。
27. Schema v5：agent runs、approvals、patches、contracts、architecture docs、semantic generations、strategy rules、evolution variants。
28. 四层 Agent、scoped source read、Kernel Budget、private score。
29. 语义记忆生成、stale auto-review、revert lessons。
30. 架构契约、契约影响、架构文档/ADR 索引。
31. propose → approve → apply 补丁工作流。
32. 策略蒸馏、离线变体评分、HTTP JSON-RPC Server。
33. 并行解析和基础 call edge 抽取。
34. tree-sitter 深度解析后端。
35. SQLite FTS5 hybrid search。
36. OS event watchfiles daemon。
37. Git / PR 发布工作流。
38. 生产评估 harness。
39. 变体提升 / 回滚审批。

## 下一步

1. 构建 10 个真实调用关系的验收用例。
2. 在真实大仓验证工作区检测成本和边界。
3. 在真实大仓验证 tree-sitter、hybrid search、daemon 和 PR 工作流。
4. 用真实项目验证 ambiguous 引用的干扰率。
5. 在真实大仓验证 Router accuracy、导出传播和 daemon 成本。
6. 用真实大仓评估 LLM Worker、语义记忆和影响传播质量。

## 阅读顺序

1. `README.md`
2. `docs/design.md`
3. `docs/roadmap.md`
4. `docs/adr/0001-local-sqlite-and-mcp.md`
5. `docs/adr/0002-deterministic-router-first.md`
6. `docs/adr/0003-deterministic-end-to-end-pipeline.md`
7. `docs/adr/0004-kernel-agents-and-approved-changes.md`

