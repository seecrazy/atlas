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

## 下一步

1. 构建 10 个真实调用关系的验收用例。
2. 设计未提交变更的可靠检测策略。
3. 为 Go/Java/Rust parser 增加方法级 receiver 解析。
4. 用真实项目验证 ambiguous 引用的干扰率。
5. 收集 20 条真实问题，验证确定性 Router 的误路由率。
6. 将上下文包接入实际 Worker Agent 会话。
7. 用真实需求任务验证汇聚输出是否可交付。

## 阅读顺序

1. `README.md`
2. `docs/design.md`
3. `docs/roadmap.md`
4. `docs/adr/0001-local-sqlite-and-mcp.md`

