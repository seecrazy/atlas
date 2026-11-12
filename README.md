# ATLAS

ATLAS 是一个面向超大项目的分层项目知识系统。它把项目组织为「子系统 → 仓库 → 模块 → 文件/符号」，通过本地索引、项目记忆和 MCP 工具回答定位、引用、影响范围和上下文检索类问题。

## 当前状态

项目处于 **Phase 5 完整本地 MVP**：多仓基础索引、Python/TypeScript/JavaScript/Go/Java/Rust 符号与基础 call edge 抽取、SQLite 存储、MCP stdio / HTTP Server、Git-aware 增量索引、未提交工作区检测、引用解析置信度、确定性 Router、四层 Agent、Kernel Budget/审批、语义记忆、架构契约与文档、审批补丁工作流、离线进化评估和审计记录已经可用。

运行环境要求 Python 3.9+。

当前不包含：

- tree-sitter 级深度语言解析
- embedding / hybrid search
- PR / Git remote 自动创建
- OS event 文件监听
- LLM Worker 在真实大仓的质量评估

## 快速开始

```bash
cd atlas
python3 -m pip install -e .

# 在任意项目根目录执行
atlas init /path/to/project
atlas index /path/to/project
atlas health /path/to/project

# 生成健康度报告
atlas report /path/to/project
atlas report /path/to/project --format markdown --output /tmp/atlas-health.md --create-parents

# 评估 Router
atlas validate-router /path/to/project

# 运行确定性端到端编排
atlas run /path/to/project "Which callers reference RetryClient?"

# 生成导出符号传播报告
atlas propagate /path/to/project

# 前台轮询增量索引
atlas daemon /path/to/project --interval 5 --max-iterations 1

# 只处理 commit-to-commit 变化，不扫描未提交工作区
atlas index /path/to/project --no-workspace

# 强制全量重建（Git 增量索引跳过时使用）
atlas index /path/to/project --full

# 启动 MCP stdio server
atlas serve /path/to/project

# 启动 HTTP JSON-RPC server（可用 ATLAS_HTTP_TOKEN 保护）
atlas serve-http /path/to/project --port 8787

# 运行四层 Agent（无 LLM 配置时自动使用确定性 Worker）
atlas agents /path/to/project "Read the implementation of execute_with_backoff"

# 语义记忆
atlas memory generate /path/to/project
atlas memory auto-review /path/to/project
atlas memory lessons /path/to/project

# 架构契约与文档
atlas architecture sync /path/to/project
atlas architecture contracts /path/to/project
atlas architecture impact /path/to/project --name OrderService.create
atlas architecture docs-index /path/to/project
atlas architecture docs /path/to/project

# Kernel 审批
atlas approvals list /path/to/project --status pending

# 补丁工作流
atlas patch propose /path/to/project --file repo/src/a.py --old-text "def a():" --new-text "def a():  # changed" --title "change a" --created-by agent
atlas approvals decide /path/to/project 1 approved --approver human --reason "reviewed"
atlas patch apply /path/to/project PATCH_KEY --applied-by human --test-command "true"

# 离线进化
atlas evolution distill /path/to/project
atlas evolution create /path/to/project --object-type router_terms --name timeout-terms --payload '{"add_terms":["timeout"]}'
atlas evolution evaluate /path/to/project VARIANT_KEY
```

建议把 `.atlas/` 加入目标项目的 `.gitignore`，避免提交本地索引数据库和 scope token。

## 引用置信度

引用结果区分四类解析状态：

| 状态 | 含义 |
| --- | --- |
| `resolved` | 限定名精确匹配符号 |
| `unique_name` | 目标最后一段在索引中唯一匹配 |
| `ambiguous` | 有多个同名候选，只保留低置信边 |
| `unresolved` | 只能确认 import/use 语法，未绑定符号 |

`impact` 会包含低置信边，但结果中的 `confidence` 和 `resolution_status` 可用于下游 Agent 决定是否人工复核。

设置 `min_confidence` 可以在服务端过滤噪声边。返回结果仍会说明被过滤的数量，避免静默丢数据。

## Router

`atlas_route` 是确定性 Router 原型，不调用 LLM。它根据问题关键词、索引候选和引用信号输出 `intent`、`role`、`confidence`、推荐工具、scope 候选和 warning。

当前 Router 不执行代码修改，也不直接读取函数体。

## 四层 Agent

```bash
atlas agents /path/to/project "Read the implementation of execute_with_backoff"
```

运行顺序是 architect → system → module → scoped developer。Developer Agent 会通过服务端 scope token 读取物理文件片段；每个 Agent 运行受时间、调用和 token 预算约束。

无 LLM 配置时使用确定性 claim。配置 OpenAI-compatible LLM 时，通过环境变量提供：

```bash
export ATLAS_LLM_BASE_URL="https://api.example.com/v1"
export ATLAS_LLM_API_KEY="..."
export ATLAS_LLM_MODEL="..."
```

## 上下文包

ATLAS 提供三层结构化上下文包，帮助不同粒度的 Agent 只接收自己职责内的信息：

1. `atlas_system_context`：仓库、子系统、跨仓引用边和统计。
2. `atlas_module_context`：目标文件的符号、import、入向引用和相关文件。
3. `atlas_developer_context`：目标文件/符号的窄化上下文和 scope 候选。

这些工具不返回完整源码。开发 Agent 访问完整实现仍应通过外部文件工具，并使用 scope token 限制边界。

## 回溯汇聚

`atlas_converge` 接收 developer / module / system / architect 四层 Worker 的结构化发现，按 developer → module → system → architect 的顺序汇聚。每条发现必须携带索引中的文件证据；可选 `impact_symbol` 会生成影响传播链。

汇聚器只做结构化汇总和证据校验，不生成代码，也不自动提交修改。

## Kernel、审批和补丁

Kernel 限制 Agent 时间、调用次数和 token 数；finding 必须通过 evidence verifier。`apply_patch` 是高风险动作，必须经过 `propose → human approve → apply`。可选 test command 失败时不会写入文件。

## 端到端编排

```bash
atlas run /path/to/project "Which callers reference RetryClient?"
```

`atlas run` 执行：

1. `atlas_route` 分类问题。
2. 构建系统 / 模块 / 开发上下文包。
3. 运行确定性结构 Worker。
4. 调用 `atlas_converge` 汇总。

它不调用 LLM，不读取函数体，不修改代码。

## 导出符号传播

```bash
atlas propagate /path/to/project
```

索引器记录导出符号的 `added` / `removed` 变更历史，报告每个变更符号的下游引用和受影响仓库。

## Router 验证

```bash
atlas validate-router /path/to/project
```

ATLAS 内置 20 条冻结路由用例。结果写入审计事件，并出现在 `atlas health` 与 `atlas report` 中。

## 架构知识

在 `.atlas/config.json` 中声明 contracts 和 doc_paths 后，使用 `atlas architecture sync` 与 `atlas architecture docs-index` 建立契约和 ADR/API/架构文档索引。

### Codex CLI 接入

```bash
codex mcp add atlas -- atlas-mcp --project-root /absolute/path/to/project
```

或在 `~/.codex/config.toml` 中加入：

```toml
[mcp_servers.atlas]
command = "atlas-mcp"
args = ["--project-root", "/absolute/path/to/project"]
```

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `atlas_health` | 查看索引覆盖率、文件数、符号数、记忆状态 |
| `atlas_search` | 按关键词搜索符号和文件路径 |
| `atlas_symbols` | 查询文件或路径下的符号 |
| `atlas_references` | 查询符号引用 |
| `atlas_impact` | 评估修改某符号时受影响的下游位置 |
| `atlas_file_context` | 返回文件的符号结构摘要，不返回完整代码 |
| `atlas_route` | 对问题做确定性路由分类 |
| `atlas_system_context` | 构建子系统/仓库与跨仓引用包 |
| `atlas_module_context` | 构建文件符号边界和依赖包 |
| `atlas_developer_context` | 构建目标符号的窄化开发上下文 |
| `atlas_impact_chain` | 输出两级影响传播链 |
| `atlas_converge` | 按角色上行汇聚结构化发现 |
| `atlas_export_propagation` | 报告导出符号变更的下游引用 |
| `atlas_pipeline` | 串联路由、上下文、Worker 和汇聚 |
| `atlas_memories` | 查询记忆及证据状态 |
| `atlas_review_memory` | 提交记忆复核决策 |
| `atlas_create_scope` | 创建短期受限访问 token |
| `atlas_scoped_search` | 携带 scope token 在授权范围内查询 |
| `atlas_layered_agents` | 运行四层 Agent |
| `atlas_generate_memories` | 生成语义记忆 |
| `atlas_auto_review_memories` | 自动复核 stale 记忆 |
| `atlas_extract_lessons` | 提取 Git revert 教训 |
| `atlas_sync_contracts` | 同步架构契约 |
| `atlas_contract_impact` | 评估契约影响 |
| `atlas_index_architecture_docs` | 索引架构文档 |
| `atlas_approvals` | 查看审批 |
| `atlas_decide_approval` | 决策审批 |
| `atlas_propose_patch` | 提出审批补丁 |
| `atlas_apply_patch` | 应用已批准补丁 |
| `atlas_distill_strategies` | 蒸馏路由策略 |
| `atlas_evaluate_variant` | 离线评估变体 |

`atlas_create_scope` 当前用于编排层能力验证。生产化前会改为只有受信任的编排器可调用，并增加撤销、频率限制和完整审计策略。

## 文档

- [设计方案](docs/design.md)
- [路线图](docs/roadmap.md)
- [架构决策记录](docs/adr/0001-local-sqlite-and-mcp.md)
- [确定性 Router ADR](docs/adr/0002-deterministic-router-first.md)
- [端到端 Pipeline ADR](docs/adr/0003-deterministic-end-to-end-pipeline.md)
- [Kernel / Agent / Changes ADR](docs/adr/0004-kernel-agents-and-approved-changes.md)
- [交接记录](docs/HANDOFF.md)

## 开发

```bash
cd atlas
python3 -m pytest
```

