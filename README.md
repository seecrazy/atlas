# ATLAS

ATLAS 是一个本地优先的分层项目知识系统。它把项目组织为：

```text
Subsystem → Repository → Module → File / Symbol
```

并通过 SQLite、语义记忆、影响分析、四层 Agent 和 MCP 工具回答四类问题：

1. 它在哪里？
2. 谁引用它？
3. 修改它会影响哪里？
4. 这个粒度的上下文边界是什么？

ATLAS 不要求把整仓源码塞进模型上下文；它先构建可验证的结构索引，再按角色注入窄化上下文。

## 当前状态

| 状态 | 说明 |
| --- | --- |
| 版本 | `0.2.0` |
| 阶段 | 本地生产可用 MVP |
| 运行面 | CLI、MCP stdio、MCP HTTP |
| 存储 | 本地 SQLite / WAL |
| 代码智能 | Python AST + 多语言 tree-sitter / lightweight parser |
| 质量门槛 | `pytest`、`doctor`、Router / Agent / Semantic evaluation |
| 部署形态 | 单机本地服务，不是多用户共享服务 |

当前已实现：

- 多仓结构索引：Python、TypeScript、JavaScript、Go、Java、Rust
- 可选 tree-sitter 深度解析后端
- Git-aware 增量索引与未提交工作区检测
- 符号签名、导出 API 变更、signature change 检测
- 引用解析、置信度和影响传播链
- SQLite FTS5 hybrid search
- 本地向量 embedding 与语义记忆
- 架构契约、ADR / 架构文档索引
- 确定性 Router、四层 Agent 与可选 LLM Worker
- Kernel Budget、审批、evidence verifier
- 审批补丁、本地测试、Git worktree 发布
- MCP stdio / HTTP JSON-RPC
- trace_id、运行指标、健康报告、daemon 锁与 backoff
- 离线进化评估、策略提升与回滚

在真实大仓使用前，建议运行 `atlas doctor`、`atlas index` 和 `atlas evaluate`，确认解析覆盖率、影响准确率和性能。

## 目录

1. [当前状态](#当前状态)
2. [谁适合使用](#谁适合使用)
3. [设计原则](#设计原则)
4. [系统结构](#系统结构)
5. [安装](#安装)
6. [快速开始](#快速开始)
7. [配置](#配置)
8. [CLI 速查](#cli-速查)
9. [MCP](#mcp)
10. [典型工作流](#典型工作流)
11. [质量与基准](#质量与基准)
12. [LLM Worker](#llm-worker)
13. [安全模型](#安全模型)
14. [故障排查](#故障排查)
15. [文档](#文档)
16. [开发与贡献](#开发与贡献)
17. [Roadmap](#roadmap)
18. [License](#license)

## 谁适合使用

### 适合

- 架构师需要理解多仓依赖、契约和影响范围。
- 平台团队要把代码结构、语义记忆和安全审批接入 AI Agent。
- 开发者在本地 IDE / CLI 中定位调用方、影响范围和上下文。
- 企业想在执行补丁前保留人类审批和可审计边界。

### 不适合

- 需要多用户共享同一份索引的在线服务。
- 需要全公司权限集成、SSO、RBAC 的场景。
- 需要跨机器实时索引同步的场景。
- 希望直接把整仓代码交给模型、不需要结构索引的场景。

## 设计原则

1. **结构优先**：先索引确定性事实，再让模型推理。
2. **证据优先**：finding 必须绑定文件和行号。
3. **分层隔离**：架构、系统、模块、实现各看各的上下文。
4. **权限服务端校验**：scope token 不依赖客户端自觉。
5. **安全闭环**：补丁必须经过 propose → approve → apply。
6. **可观测**：路由、Agent、补丁、发布和策略变更都可审计。
7. **LLM 可选**：无模型时使用确定性 Worker，有模型时增强推理。

## 系统结构

```text
           ┌──────────────┐
question → │   Router     │ → locate / impact / implementation / memory
           └──────┬───────┘
                  ↓
       ┌──────────────────────┐
       │ System Context Layer │  repos, contracts, deployment boundaries
       └──────────┬───────────┘
                  ↓
       ┌──────────────────────┐
       │ Module Context Layer │  symbols, imports, inbound refs
       └──────────┬───────────┘
                  ↓
       ┌──────────────────────┐
       │ Scoped Developer     │  source access with scope token
       └──────────┬───────────┘
                  ↓
       ┌──────────────────────┐
       │ Upward Convergence   │  evidence-verified findings
       └──────────────────────┘
```

## 安装

```bash
cd atlas
python3 -m pip install -e .
```

基础功能只依赖 Python 标准库。生产增强功能推荐安装可选依赖：

```bash
python3 -m pip install -e '.[production]'
```

| 包 | 用途 |
| --- | --- |
| `tree-sitter-language-pack` | 深度语法解析 |
| `watchfiles` | OS event 文件监听 |

运行环境要求 Python 3.9+。

## 快速开始

```bash
# 初始化目标项目
atlas init /absolute/path/to/project

# 全量或增量索引
atlas index /absolute/path/to/project --full
atlas index /absolute/path/to/project

# 检查运行环境、依赖和数据库
atlas doctor /absolute/path/to/project

# 查看健康状态
atlas health /absolute/path/to/project

# 搜索
atlas search /absolute/path/to/project "retry timeout" --hybrid

# 运行端到端确定性 Pipeline
atlas run /absolute/path/to/project "Which callers reference RetryClient?"
```

建议把 `.atlas/` 加入目标项目 `.gitignore`，避免提交索引数据库、scope token 和本地锁。

## 配置

配置位于：

```text
/absolute/path/to/project/.atlas/config.json
```

最小示例：

```json
{
  "project": "my-platform",
  "subsystems": [
    {
      "name": "payment",
      "repos": ["payment-core", "payment-gateway"]
    }
  ],
  "languages": ["python", "typescript", "go"],
  "exclude": ["node_modules", "dist", "target", ".venv"],
  "doc_paths": ["docs"],
  "max_workers": 4,
  "parser_backend": "auto",
  "budgets": {
    "max_seconds": 300,
    "max_calls": 50,
    "max_tokens": 50000
  },
  "contracts": [
    {
      "name": "OrderService.create",
      "provider_repo": "order-core",
      "version": "v1",
      "consumers": ["payment-core", "risk-core"]
    }
  ],
  "allowed_test_commands": [
    "pytest",
    "python3 -m pytest"
  ]
}
```

| 字段 | 用途 |
| --- | --- |
| `subsystems` | 子系统到仓库的映射 |
| `languages` | 允许索引的语言 |
| `exclude` | 文件/目录排除规则 |
| `doc_paths` | 架构文档、ADR、API 文档路径 |
| `contracts` | 显式契约及其 consumers |
| `max_workers` | 文件解析并行度 |
| `parser_backend` | `auto` / `tree-sitter` / `builtin` |
| `budgets` | Agent 运行时间、调用和 token 上限 |
| `allowed_test_commands` | 补丁应用前允许执行的精确测试命令 |

## CLI 速查

### 索引与健康

```bash
atlas index /path/to/project
atlas index /path/to/project --full
atlas index /path/to/project --no-workspace

atlas health /path/to/project
atlas report /path/to/project
atlas report /path/to/project \
  --format markdown \
  --output /tmp/atlas-health.md \
  --create-parents

atlas doctor /path/to/project
```

`index` 默认处理 commit diff 和未提交工作区变化。`--full` 强制重建；`--no-workspace` 只处理 commit-to-commit 变化。

### 搜索

```bash
atlas search /path/to/project "executeWithBackoff"
atlas search /path/to/project "retry timeout" --hybrid
atlas embed rebuild /path/to/project
atlas embed search /path/to/project "retry policy"
```

### 端到端运行

```bash
atlas run /path/to/project "Which callers reference RetryClient?"
```

`run` 输出 route、system/module/developer context、findings、convergence、trace_id 和 budget。默认不调用 LLM，不读取函数体，不修改代码。

四层 Agent 包括 scoped source read：

```bash
atlas agents /path/to/project "Read the implementation of executeWithBackoff"
```

### 导出符号传播

```bash
atlas propagate /path/to/project
```

报告导出符号的 `added` / `removed` / `signature_changed`，以及下游引用和受影响仓库。

### 语义记忆

```bash
atlas memory generate /path/to/project
atlas memory generate /path/to/project --scope payment-core
atlas memory auto-review /path/to/project
atlas memory lessons /path/to/project
```

`generate` 默认生成确定性结构摘要；配置 LLM 后可生成自然语言摘要，LLM 失败会自动回退。

### 架构契约与文档

```bash
atlas architecture sync /path/to/project
atlas architecture contracts /path/to/project
atlas architecture impact /path/to/project --name OrderService.create
atlas architecture docs-index /path/to/project
atlas architecture docs /path/to/project
```

### 审批与补丁

```bash
atlas patch propose /path/to/project \
  --file payment-core/src/retry/handler.py \
  --old-text "def execute(task):" \
  --new-text "def execute(task, max_attempts=None):" \
  --title "Add retry cap" \
  --created-by agent

atlas approvals list /path/to/project --status pending
atlas approvals decide /path/to/project 1 approved \
  --approver human \
  --reason "reviewed"

atlas patch apply /path/to/project PATCH_KEY --applied-by human
```

`test_command` 必须精确出现在 `allowed_test_commands` 中。默认 allowlist 包含 `pytest`、`python3 -m pytest`、`npm test`、`go test ./...`、`mvn test` 等。命令失败时不会写入文件。

### 分支与 PR

```bash
atlas patch propose-publish /path/to/project PATCH_KEY \
  --branch feature/atlas-change \
  --title "Add retry cap" \
  --requested-by agent

atlas approvals decide /path/to/project 2 approved \
  --approver human \
  --reason "approved for PR"

atlas patch publish /path/to/project PATCH_KEY \
  --branch feature/atlas-change \
  --title "Add retry cap" \
  --approval-key APPROVAL_KEY \
  --published-by human
```

发布使用临时 `git worktree`，不会切换或污染当前工作区。创建 PR 需要 GitHub CLI `gh` 已安装并登录。

### Daemon

```bash
atlas daemon /path/to/project --interval 5 --max-iterations 1
atlas daemon /path/to/project --watch --debounce-ms 300
```

daemon 使用 `.atlas/daemon.lock` 防止重复运行，失败时指数退避，每次循环心跳更新锁文件。

### 离线进化

```bash
atlas evolution distill /path/to/project
atlas evolution create /path/to/project \
  --object-type strategy_rule \
  --name timeout-impact \
  --payload '{"pattern":"timeout","intent":"impact","role":"system","utility_score":0.85}'

atlas evolution evaluate /path/to/project VARIANT_KEY
atlas evolution shadow /path/to/project VARIANT_KEY

atlas evolution propose-promotion /path/to/project VARIANT_KEY --requested-by agent
atlas evolution promote /path/to/project VARIANT_KEY \
  --approval-key APPROVAL_KEY --promoted-by human

atlas evolution propose-rollback /path/to/project VARIANT_KEY \
  --requested-by human --reason "bad effect"
atlas evolution rollback /path/to/project VARIANT_KEY \
  --approval-key APPROVAL_KEY --rolled-back-by human
```

进化只在离线边界评分。提升和回滚都需要高危审批。

### 运维

```bash
atlas maintenance prune /path/to/project \
  --retention-days 90 \
  --max-events 200000

atlas doctor /path/to/project
```

`doctor` 检查 Python、SQLite FTS5、git、gh、tree-sitter、watchfiles 和数据库 schema。

## MCP

### MCP stdio

```bash
atlas serve /path/to/project
```

### MCP HTTP JSON-RPC

```bash
atlas serve-http /path/to/project --host 127.0.0.1 --port 8787
```

设置 `ATLAS_HTTP_TOKEN` 后，请求必须携带：

```http
Authorization: Bearer <token>
```

HTTP server 默认绑定 `127.0.0.1`，有请求体大小限制和简单限流。

### 工具分组

| 组 | 工具 |
| --- | --- |
| 索引 | `atlas_health`, `atlas_search`, `atlas_hybrid_search`, `atlas_symbols`, `atlas_file_context` |
| 影响分析 | `atlas_references`, `atlas_impact`, `atlas_impact_chain`, `atlas_export_propagation` |
| 编排 | `atlas_route`, `atlas_system_context`, `atlas_module_context`, `atlas_developer_context`, `atlas_pipeline`, `atlas_layered_agents` |
| 记忆 | `atlas_memories`, `atlas_generate_memories`, `atlas_auto_review_memories`, `atlas_extract_lessons` |
| 架构 | `atlas_sync_contracts`, `atlas_contracts`, `atlas_contract_impact`, `atlas_index_architecture_docs`, `atlas_architecture_docs` |
| Kernel / Patch | `atlas_approvals`, `atlas_decide_approval`, `atlas_propose_patch`, `atlas_decide_patch`, `atlas_apply_patch`, `atlas_publish_patch` |
| 进化 | `atlas_distill_strategies`, `atlas_evaluate_variant`, `atlas_promote_variant`, `atlas_rollback_variant` |

完整清单使用 MCP `tools/list` 获取。

## 典型工作流

### 1. 熟悉仓库

```bash
atlas index .
atlas run . "What are the main modules and contracts?"
```

### 2. 定位调用方和影响

```bash
atlas search . "OrderService.create" --hybrid
atlas propagate .
atlas run . "If I change OrderService.create, what breaks?"
```

### 3. 审批补丁

```bash
atlas patch propose . --file src/a.py   --old-text "def run():" --new-text "def run(dry_run=False):"   --title "Add dry run" --created-by agent

atlas approvals decide . 1 approved --approver human --reason "reviewed"
atlas patch apply . PATCH_KEY --applied-by human
```

### 4. 观察 API signature change

```bash
atlas index .
atlas propagate .
```

关注 `signature_changed`，这类变更应触发 contract impact 检查。

## LLM Worker

默认 Agent 使用确定性 Worker，无外部模型也能运行。

如需 LLM 推理，配置 OpenAI-compatible API：

```bash
export ATLAS_LLM_BASE_URL="https://api.example.com/v1"
export ATLAS_LLM_API_KEY="..."
export ATLAS_LLM_MODEL="..."
```

LLM 不可用时会自动回退到确定性结果。API key 只从环境变量读取，不写入 `.atlas/config.json`。

评估：

```bash
atlas evaluate /path/to/project --no-llm
atlas evaluate /path/to/project
```

## 安全模型

1. Agent 运行受时间、调用次数和 token 预算限制。
2. 所有 finding 必须携带索引内文件证据。
3. `apply_patch` 必须经过 `propose → human approve → apply`。
4. `test_command` 使用精确 allowlist，不接受任意 shell 命令。
5. scoped source read 解析 real path，并拒绝 symlink 逃逸。
6. PR 发布使用临时 Git worktree。
7. 审批、读取、补丁、发布和策略变更写入审计。

## 质量与基准

ATLAS 自项目已完成基础评估，完整结果见 [ATLAS Self Evaluation](docs/evaluation/ATLAS_SELF.md)。

| 指标 | 基线 |
| --- | ---: |
| Router intent accuracy | 1.0000 |
| Router role accuracy | 1.0000 |
| Agent hit rate | 1.0000 |
| Agent evidence coverage | 1.0000 |
| Semantic recall | 0.9524 |
| Semantic precision | 0.4778 |
| Semantic F1 | 0.6126 |

这个结果证明链路可用，但 semantic precision 仍受确定性模板影响；进入真实大仓前应建立 golden set。

### 必测指标

| 指标 | 目的 |
| --- | --- |
| symbol recall | 确认目标符号是否被索引 |
| reference precision / recall | 衡量引用边质量 |
| unresolved ratio | 发现 import / call resolver 短板 |
| impact precision | 衡量影响分析误报率 |
| agent hit rate | 衡量 Agent 是否命中目标符号 |
| evidence coverage | 衡量 finding 是否可追溯 |
| index time | 衡量全量 / 增量索引成本 |
| MCP p95 | 衡量查询延迟 |
| daemon CPU / IO | 衡量后台成本 |

## 故障排查

| 症状 | 处理 |
| --- | --- |
| `doctor` 显示 FTS5 unavailable | 确认 SQLite 编译包含 FTS5 |
| `tree_sitter not installed` | 安装 `.[deep-parser]` |
| `watchfiles not installed` | 使用轮询 daemon 或安装 `.[watch]` |
| daemon 无法启动 | 检查 `.atlas/daemon.lock` 是否有活进程持有 |
| 引用大量 unresolved | 先建立 import resolver golden set，再运行 `atlas index --full` |
| `gh` 不可用 | PR 功能需要安装并登录 GitHub CLI |
| schema_version 过低 | 运行 `atlas index` 触发迁移 |
| 结果过旧 | 运行 `atlas index --full` 后重新评估 |

## 文档

| 文档 | 内容 |
| --- | --- |
| [设计方案](docs/design.md) | 架构、数据模型、安全边界 |
| [路线图](docs/roadmap.md) | 已完成与剩余事项 |
| [ATLAS 自项目评估](docs/evaluation/ATLAS_SELF.md) | 真实项目验证结果 |
| [ADR 0001](docs/adr/0001-local-sqlite-and-mcp.md) | SQLite + MCP |
| [ADR 0002](docs/adr/0002-deterministic-router-first.md) | 确定性 Router |
| [ADR 0003](docs/adr/0003-deterministic-end-to-end-pipeline.md) | 端到端 Pipeline |
| [ADR 0004](docs/adr/0004-kernel-agents-and-approved-changes.md) | Kernel / Agent / 补丁 |
| [交接记录](docs/HANDOFF.md) | 当前实现与下一步 |
| [CONTRIBUTING](CONTRIBUTING.md) | 开发与提交要求 |
| [SECURITY](SECURITY.md) | 支持范围与漏洞报告 |

## 开发

```bash
cd atlas
python3 -m pytest -q
```

当前测试覆盖索引、解析、引用、影响、搜索、Agent、Kernel、补丁、进化、运维和 MCP。

### 本地命令

```bash
# 安装开发环境
python3 -m pip install -e '.[production]'

# 运行测试
python3 -m pytest -q

# 自检
PYTHONPATH=src python3 -m atlas.cli doctor .

# 自项目评估
PYTHONPATH=src python3 -m atlas.cli index . --full
PYTHONPATH=src python3 -m atlas.cli evaluate . --no-llm
```

## 贡献

提交前请检查：

1. `python3 -m pytest -q` 通过。
2. `git diff --check` 无空白错误。
3. 新功能有测试。
4. 用户可见行为已更新 README。
5. 设计变化已同步 `docs/design.md`。
6. 完成状态已同步 `docs/roadmap.md`。
7. 交接要点已同步 `docs/HANDOFF.md`。

## Roadmap

详见 [Roadmap](docs/roadmap.md)。当前重点是：

1. 多语言真实仓库 golden set。
2. LLM Worker 真实项目评估。
3. 引用 resolver 的 precision / recall 提升。
4. 大规模索引性能基准。
5. 远端 / 多人共享部署形态。

## License

License 尚未正式选定。如果准备开源发布，建议选择 MIT 或 Apache-2.0，并补齐 LICENSE 文件。
