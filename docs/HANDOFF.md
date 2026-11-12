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

已实现 Phase 1：

1. `.atlas/config.json` 项目配置。
2. `.atlas/index.db` SQLite 数据库。
3. Python AST 抽取。
4. TypeScript/JavaScript 基础抽取。
5. 符号、引用、文件、记忆、scope、audit 表。
6. MCP stdio 工具。
7. scope token 与路径过滤。
8. pytest 测试。

## 下一步

1. 实现 git diff-tree 增量索引。
2. 增加 Go / Java 解析器。
3. 增加索引诊断报告。
4. 设计 Router 的请求分类 schema。
5. 构建 10 个真实调用关系的验收用例。

## 阅读顺序

1. `README.md`
2. `docs/design.md`
3. `docs/roadmap.md`
4. `docs/adr/0001-local-sqlite-and-mcp.md`

