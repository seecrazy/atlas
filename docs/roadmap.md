# Roadmap

## 当前状态

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| Phase 0 | 设计收敛 | ✅ 已完成 |
| Phase 1 | 本地索引 + MCP 垂直切片 | ✅ 已完成 |
| Phase 2 | 多语言索引与可靠性 | ✅ 已完成 |
| Phase 3 | 语义记忆与复核 | ✅ 本地闭环完成 |
| Phase 4 | 四层角色编排 | ✅ 本地闭环完成 |
| Phase 5 | 增量守护与可观测性 | ✅ 本地闭环完成 |
| Phase 6 | 受限进化 | ✅ 本地闭环完成 |
| Phase 7 | 真实项目验证 | 🟡 进行中 |
| Phase 8 | 企业运行形态 | ⏳ 未开始 |

## 成熟度

| 级别 | 状态 |
| --- | --- |
| L0 Demo | ✅ |
| L1 Local MVP | ✅ |
| L2 Verified Local | ✅ |
| L3 Real-world Validated | 🟡 |
| L4 Production Service | ⏳ |
| L5 Controlled Evolution | 🟡 |

## 已完成

### Phase 0：设计收敛

- [x] 明确垂直切片范围。
- [x] 明确本地 SQLite + MCP 运行面。
- [x] 定义 scope token 服务端校验模型。
- [x] 建立设计 / roadmap / HANDOFF 文档结构。

### Phase 1：知识库垂直切片

- [x] CLI：`init`、`index`、`health`、`report`、`serve`。
- [x] SQLite WAL 数据库与 schema 迁移。
- [x] 文件、符号、引用、记忆、scope、审计表。
- [x] MCP stdio 工具面。
- [x] pytest 覆盖核心链路。

### Phase 2：多语言索引可靠性

- [x] Python AST 解析。
- [x] TypeScript / JavaScript 基础解析。
- [x] Go / Java / Rust 基础解析。
- [x] 可选 tree-sitter deep parser。
- [x] Git commit diff + workspace diff。
- [x] 增量索引与全量重建。
- [x] 引用去重、置信度、跨仓标识。
- [x] 导出符号传播报告。
- [x] 文件级索引诊断。
- [x] Markdown / JSON 健康报告。

### Phase 3：搜索与语义记忆

- [x] SQLite FTS5 hybrid search。
- [x] 本地 hashed embedding。
- [x] vector search。
- [x] 语义记忆结构化字段。
- [x] 文件 / repo / subsystem 三层记忆。
- [x] stale 记忆自动复核。
- [x] Git revert 教训提取。
- [x] memory utility score。

### Phase 4：路由与角色编排

- [x] 确定性 Router。
- [x] 20 条冻结 Router 验证集。
- [x] system / module / developer context packet。
- [x] 确定性端到端 Pipeline。
- [x] 四层 Agent。
- [x] scoped developer file read。
- [x] 上行回溯汇聚。
- [x] OpenAI-compatible LLM Provider。
- [x] LLM 失败自动回退确定性 Worker。

### Phase 5：安全与补丁

- [x] Kernel Budget：时间 / calls / tokens。
- [x] high-risk approval。
- [x] finding evidence verifier。
- [x] private score。
- [x] 补丁 propose → approve → apply。
- [x] test command allowlist。
- [x] symlink escape protection。
- [x] Git worktree publication。
- [x] 可选 GitHub PR 创建。
- [x] MCP input schema validation。

### Phase 6：运维与可观测性

- [x] trace_id。
- [x] `run_metrics`。
- [x] daemon lock。
- [x] daemon exponential backoff。
- [x] watchfiles OS event watcher。
- [x] health / report 扩展。
- [x] doctor。
- [x] audit / metrics retention。
- [x] MCP HTTP auth、body limit、rate limit。

### Phase 7：受限进化

- [x] 策略蒸馏。
- [x] 离线变体评分。
- [x] shadow evaluation。
- [x] utility decay。
- [x] 变体提升与回滚审批。
- [x] active strategy rules。

## 当前进行中：Phase 7 真实项目验证

目标是从“功能完成”进入“质量被测量”。

### 7.1 多语言 golden set

- [ ] 选择真实多语言仓库，覆盖至少 3 种语言。
- [ ] 收集 50 个已知调用关系。
- [ ] 收集 20 条真实开发者问题。
- [ ] 收集 10 个真实影响分析场景。
- [ ] 建立 expected symbols / expected references / expected impact JSON。
- [ ] 输出 symbol recall。
- [ ] 输出 reference precision / recall。
- [ ] 输出 unresolved reason 分布。
- [ ] 输出 impact precision / recall。

### 7.2 性能基准

- [ ] 记录全量索引时间。
- [ ] 记录增量索引时间。
- [ ] 记录每千文件 SQLite 体积。
- [ ] 记录 MCP query p50 / p95。
- [ ] 记录 Agent run p95。
- [ ] 记录 daemon CPU / IO / 内存。

### 7.3 语义摘要质量

- [ ] 构建正确 / 错误 / stale claim 数据集。
- [ ] 评估 deterministic claim。
- [ ] 评估 LLM claim。
- [ ] 计算 claim-level precision / recall / hallucination rate。
- [ ] 输出可复现报告。

## Next

### 8.1 LLM Worker 真实项目评估

- [ ] 配置真实 OpenAI-compatible Provider。
- [ ] 对同一组问题运行 deterministic 与 LLM。
- [ ] 记录 answer correctness。
- [ ] 记录 hallucination rate。
- [ ] 记录 evidence coverage。
- [ ] 记录 token / latency / failure rate。
- [ ] 对比 fallback 率。
- [ ] 生成评估报告。

### 8.2 引用 resolver 升级

- [ ] Python module / symbol resolver。
- [ ] TS / JS `tsconfig paths` resolver。
- [ ] barrel export resolver。
- [ ] Go package resolver。
- [ ] Java package / class resolver。
- [ ] Rust crate / module resolver。
- [ ] unresolved reason 分类。
- [ ] resolver golden set 测试。

### 8.3 运行形态

- [ ] 远端只读索引服务。
- [ ] 索引快照与版本管理。
- [ ] 多用户只读访问。
- [ ] RBAC。
- [ ] audit 远端导出。
- [ ] 生产部署文档。

## Later

### 9.1 企业集成

- [ ] SSO。
- [ ] RBAC。
- [ ] 审计导出到 SIEM。
- [ ] 多租户索引隔离。
- [ ] 索引服务 SLA。

### 9.2 PR / CI 闭环

- [ ] PR review bot。
- [ ] CI 状态回写。
- [ ] PR 影响摘要。
- [ ] 自动 review checklist。
- [ ] 策略化合并建议。

### 9.3 模型增强

- [ ] 模型 embedding。
- [ ] reranker。
- [ ] semantic cache。
- [ ] token-aware context packing。
- [ ] structured output 校验。

## 非目标

ATLAS 当前不做：

1. 分布式多用户共享索引。
2. 全量源码长期保存在索引中。
3. 无人审批的代码合并。
4. 运行时自我修改内核规则。
5. 直接替代语言服务器或构建系统。
