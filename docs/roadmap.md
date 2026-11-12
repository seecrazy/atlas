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

### Phase 3：记忆复核第一闭环

- [x] stale 记忆查询接口。
- [x] 外部审查者提交复核决策。
- [x] 复核记录与审计。

## 未完成

### Phase 3：语义与记忆

- [ ] 热点模块按需语义摘要。
- [ ] 摘要证据引用复核。
- [ ] 记忆质量评分。
- [ ] 冻结验证集。

### Phase 4：角色编排原型

- [x] Router 请求分类 schema。
- [x] 确定性 Router 原型。
- [x] 系统工程师层查询模板。
- [x] 模块工程师层上下文包构建。
- [x] 影响传播链输出。
- [x] 上行回溯汇聚格式。
- [x] 端到端确定性 Pipeline。
- [ ] 开发子任务 scoped 会话。

### Phase 5：增量守护

- [x] 前台轮询守护循环。
- [x] commit / diff 事件提取。
- [x] 导出符号变更传播。
- [ ] 文件系统监听。
- [ ] stale 记忆自动批量复核报告。

### Phase 6：受限进化

- [ ] 离线沙盒。
- [ ] 冻结评估集。
- [ ] 低/中/高三级修改授权。
- [ ] 变体评分与回滚记录。

