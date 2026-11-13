# 交接记录

## 最近更新

2026-11-13

## 当前结论

ATLAS 已完成本地生产可用 MVP。核心链路已经闭环：

```text
Index → Search / Memory → Router → Context → Agent → Convergence → Patch → Publish
```

当前版本：`0.2.0`
当前 schema：`9`
当前测试：58 passed

ATLAS 不应被描述为“只能做基础索引”。它现在具备：

1. 多语言结构索引与可选 tree-sitter。
2. FTS5 + 本地向量搜索。
3. 架构契约与文档知识源。
4. 语义记忆和 stale 复核。
5. 四层 Agent 和 scoped source read。
6. Kernel Budget 与审批。
7. 审批补丁和 Git worktree 发布。
8. trace_id、metrics、daemon lock、backoff。
9. 离线进化和策略提升 / 回滚。

## 关键边界

ATLAS 当前是单机本地系统：

1. 索引存储在项目 `.atlas/index.db`。
2. 不适合多用户共享同一份在线索引。
3. 没有 RBAC / SSO / 多租户隔离。
4. PR / Git remote 功能依赖本机 git 和 gh。
5. 还没有真实大仓、多语言 golden set 和 LLM Provider 质量评估。

## 已实现能力

### 索引与代码智能

1. 多仓注册和 subsystem → repo 映射。
2. Python AST 解析。
3. TypeScript / JavaScript / Go / Java / Rust 基础解析。
4. 可选 tree-sitter deep parser。
5. 并行文件解析。
6. Git commit diff + workspace diff。
7. 增量索引和全量重建。
8. 文件级诊断。
9. 符号 signature hash。
10. 导出 API 变更检测。
11. Python relative import resolver。
12. TS / JS relative path resolver。
13. import / call edge 置信度。

### 搜索与记忆

1. SQLite FTS5 hybrid search。
2. 本地 hashed embedding。
3. vector search。
4. 文件 / repo / subsystem 语义记忆。
5. memory structured fields。
6. memory utility score。
7. stale memory 自动复核。
8. Git revert lessons。

### 架构知识

1. architecture contracts。
2. contract provider / version / consumers。
3. contract impact analysis。
4. architecture docs / ADR / API 文档索引。
5. doc 分类与 hybrid retrieval。

### Agent 与 Kernel

1. 确定性 Router。
2. system / module / developer context packets。
3. 四层 Agent。
4. scoped physical file read。
5. Kernel Budget。
6. approval workflow。
7. evidence verifier。
8. private score。
9. OpenAI-compatible LLM Provider。
10. LLM 失败自动回退。

### 代码修改与发布

1. patch propose。
2. human approve。
3. patch apply。
4. test command allowlist。
5. target path containment。
6. symlink escape protection。
7. temporary Git worktree publication。
8. branch / commit / push / PR。
9. publication audit。

### 运维

1. trace_id。
2. run_metrics。
3. daemon lock。
4. daemon exponential backoff。
5. watchfiles OS event watcher。
6. health / report。
7. doctor。
8. audit retention。
9. metrics retention。
10. MCP HTTP auth / body limit / rate limit。

### 进化

1. strategy distillation。
2. variant scoring。
3. shadow evaluation。
4. utility decay。
5. promotion approval。
6. rollback approval。
7. active strategy rules。

## 当前质量基线

ATLAS 自项目评估结果：

| 指标 | 值 |
| --- | ---: |
| Router intent accuracy | 1.0000 |
| Router role accuracy | 1.0000 |
| Agent hit rate | 1.0000 |
| Agent evidence coverage | 1.0000 |
| Semantic recall | 0.9524 |
| Semantic precision | 0.4778 |
| Semantic F1 | 0.6126 |

自项目也暴露出引用 unresolved 率仍然较高，因此下一步不是继续堆功能，而是建立真实大仓 golden set。

## 下一步

### Phase 7：真实项目验证

1. 选择真实多语言仓库。
2. 建立 golden set。
3. 测量 reference precision / recall。
4. 测量 impact precision / recall。
5. 测量 index / MCP / Agent 性能。
6. 输出可复现评估报告。

### Phase 8：LLM Worker 实测

1. 配置真实 OpenAI-compatible Provider。
2. 对同一问题集运行 deterministic 与 LLM。
3. 测量 correctness、hallucination rate、cost、latency。
4. 对比 evidence coverage 和 fallback 率。
5. 生成评估报告。

### Phase 9：引用 resolver 升级

1. Python module / symbol resolver。
2. TS / JS paths resolver。
3. Go package resolver。
4. Java class resolver。
5. Rust crate / module resolver。
6. unresolved reason 分类。

### Phase 10：企业运行形态

1. 远端索引服务。
2. 索引快照。
3. 多用户只读访问。
4. RBAC / SSO。
5. audit export。
6. CI 回写。

## 阅读顺序

1. `README.md`
2. `docs/design.md`
3. `docs/roadmap.md`
4. `docs/evaluation/ATLAS_SELF.md`
5. `docs/adr/0001-local-sqlite-and-mcp.md`
6. `docs/adr/0002-deterministic-router-first.md`
7. `docs/adr/0003-deterministic-end-to-end-pipeline.md`
8. `docs/adr/0004-kernel-agents-and-approved-changes.md`

## 常用验收命令

```bash
python3 -m pytest -q
PYTHONPATH=src python3 -m atlas.cli doctor .
PYTHONPATH=src python3 -m atlas.cli index . --full
PYTHONPATH=src python3 -m atlas.cli validate-router .
PYTHONPATH=src python3 -m atlas.cli evaluate . --no-llm
PYTHONPATH=src python3 -m atlas.cli propagate .
```
