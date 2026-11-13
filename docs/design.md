# ATLAS 设计方案

## 1. 目标

### 目标

1. 将超大项目结构组织为「子系统 → 仓库 → 模块 → 文件/符号」四层。
2. 通过本地 SQLite 和 MCP 工具提供低延迟、可验证的结构查询能力。
3. 支持多仓注册、跨仓引用、影响分析和基础记忆漂移。
4. 使用服务端短期 scope token 控制子任务访问边界。
5. 所有索引查询、scope 授权、记忆状态变化和高危操作留下审计记录。
6. 通过 L1 内核预算、审批和验证约束四层 Agent。
7. 生成结构证据驱动的语义记忆，并支持 stale 自动复核。
8. 支持架构契约、ADR/文档知识源和契约影响分析。
9. 支持可选 LLM Worker；无 LLM 配置时自动退回确定性 Worker。
10. 支持受审批约束的本地补丁工作流和离线受限进化。

## 2. 用户流程

### 初始化与索引

1. 开发者在项目根目录创建 `.atlas/config.json`，描述子系统、仓库和语言。
2. 运行 `atlas init` 创建数据库与配置。
3. 运行 `atlas index` 扫描仓库文件，抽取符号和导入关系。
4. MCP 客户端通过 `atlas serve` 连接本地知识库。

### 查询与下钻

1. 客户端先用 `atlas_search` 或 `atlas_symbols` 定位目标。
2. 需要评估影响时调用 `atlas_references` 或 `atlas_impact`。
3. 需要限定子任务访问范围时，由编排器创建 scope token。
4. 子任务携带 token 调用 `atlas_scoped_search`，服务端校验路径范围。
5. 结果返回结构化摘要：符号名、路径、行号、引用关系和状态。

### 记忆复核

1. 索引器发现文件内容 hash 或 mtime 变化。
2. 查找引用该文件的记忆条目。
3. 将对应记忆标记为 `stale`。
4. 后续检索返回 stale 警告，由上层 Agent 决定是否复核。

## 3. 数据与状态模型

### 配置

当前 Phase 1 使用 JSON 配置，避免引入 YAML 解析依赖：

```json
{
  "project": "my-platform",
  "subsystems": [
    {
      "name": "payment",
      "repos": ["payment-core"]
    }
  ],
  "languages": ["python", "typescript", "javascript", "go", "java", "rust"],
  "exclude": ["node_modules", "dist", "target", ".venv"],
  "doc_paths": ["docs", "adr"],
  "max_workers": 4,
  "budgets": {"max_seconds": 300.0, "max_calls": 50, "max_tokens": 50000},
  "contracts": [
    {
      "name": "OrderService.create",
      "provider_repo": "order-core",
      "version": "v1",
      "consumers": ["payment-core", "risk-core"]
    }
  ],
  "llm": {
    "provider": "none",
    "base_url_env": "ATLAS_LLM_BASE_URL",
    "api_key_env": "ATLAS_LLM_API_KEY",
    "model_env": "ATLAS_LLM_MODEL"
  }
}
```

### SQLite 表

| 表 | 作用 |
| --- | --- |
| `repos` | 注册仓库及其所属子系统 |
| `files` | 文件路径、语言、内容 hash、mtime、文件级模块名 |
| `symbols` | 符号名、限定名、类型、路径、行号范围、导出标记 |
| `refs` | 源文件对目标符号的引用边 |
| `refs` 字段 | `target_symbol_id`、`resolution_status`、`resolution_method`、`confidence` |
| `index_state` | 仓库最后一次成功索引的 Git commit |
| `index_diagnostics` | 文件级索引诊断 |
| `memories` | 结构化记忆条目、证据、状态、置信度 |
| `memory_evidence` | 记忆条目与文件证据的关联 |
| `memory_reviews` | 外部审查者对记忆的复核决策 |
| `scope_tokens` | 短期 token、路径边界、状态、过期时间 |
| `audit_events` | 工具调用、scope 创建、记忆漂移等审计事件 |
| `agent_runs` | 四层 Agent 运行、预算用量、结果与失败记录 |
| `approvals` | 中/高风险操作审批请求与人工决策 |
| `patches` | 补丁文本、状态、审批关联和应用记录 |
| `contracts` | 显式架构契约、provider、version、consumers |
| `architecture_docs` | 架构文档、ADR、API/database/contract 知识源 |
| `semantic_generations` | 语义记忆生成任务和 provider |
| `strategy_rules` | 从审计蒸馏出的路由策略与效用分数 |
| `evolution_variants` | 离线进化变体、冻结评分与状态 |

### 状态

| 对象 | 状态 |
| --- | --- |
| 文件 | `valid` / `changed` |
| 引用 | `resolved` / `unique_name` / `ambiguous` / `unresolved` |
| 诊断 | `warning` / `error` |
| 记忆 | `valid` / `stale` / `invalid` |
| scope token | `active` / `used` / `expired` / `revoked` |
| Agent run | `running` / `completed` / `failed` |
| 审批 | `pending` / `approved` / `rejected` |
| 补丁 | `pending` / `approved` / `rejected` / `applied` |
| 进化变体 | `candidate` / `accepted` / `rejected` |

## 4. 关键交互

### 索引

1. 遍历配置中每个仓库目录。
2. 跳过 exclude 目录、隐藏目录、二进制文件和无扩展名文件。
3. Python 文件使用 AST 抽取模块、类、函数、方法、导入。
4. TypeScript/JavaScript 文件使用基础正则抽取类、函数、导出符号和 import。
5. Go/Java/Rust 文件使用基础正则抽取包、导入、类型、函数/方法和入口符号。
6. 内容 hash 不变时保留原记录，减少重复写入。
7. 删除的文件同步移除对应符号和引用。

### 引用解析

Phase 2 在 import 边之上增加机械化的引用解析：

1. 优先用目标限定名精确匹配符号，标记为 `resolved`，置信度 0.95。
2. 精确匹配失败时，取 import 目标的最后一段做唯一名称匹配，标记为 `unique_name`，置信度 0.65。
3. 同名候选超过一个时标记为 `ambiguous`，置信度 0.45，不绑定唯一 symbol id。
4. 无法解析时保持 `unresolved`，检索时作为低置信 import 边返回。
5. 跨仓解析结果返回目标 repo 和 `cross_repo=true`。
6. 同一 source file、target、line、kind 的重复边在写入前去重。

### 增量索引

Phase 2 引入 Git-aware 增量模式：

1. Git 仓库首次索引仍然全量扫描。
2. 首次索引成功后，在 `index_state` 记录每个仓库的 `last_commit`。
3. 后续索引读取 Git 变更集合，只把变更路径作为候选文件。
4. 候选文件仍使用内容 hash 校验，避免索引器误读文件系统缓存。
5. 删除文件会从文件、符号、引用和证据关联中同步清理。
6. 非 Git 仓库、仓库没有 commit、或 Git 命令失败时降级为全量扫描，保证可用性。
7. 默认候选集合包含未提交工作区变更：
   - tracked 修改和删除通过 `git diff-index` 检测；
   - untracked 文件通过 `git ls-files --others --exclude-standard` 检测；
   - commit diff 与工作区变更合并，同名路径以工作区状态优先。
8. `full=True` 强制全量重建候选集合。
9. `no_workspace=True` 可退回到只处理 commit-to-commit 变化的旧模式。

内容 hash 仍是最终判断标准；Git 状态只用于缩小候选集合，不会把未实际变化的文件写入索引。

### 健康度报告

`atlas report` 从 SQLite 生成项目健康度报告：

1. JSON 格式给程序消费。
2. Markdown 格式给人类审阅。
3. 报告包含索引规模、语言覆盖、引用解析状态、诊断、记忆状态和警告。
4. CLI 可选择输出文件；输出目录必须已存在或使用 `--create-parents`。

### Router 验证集

Router 使用冻结验证集评估，验证集保存在包内 `router_cases.json`：

1. 共 20 条中英文问题。
2. 每条必须声明 `expected_intent`；部分声明 `expected_role`。
3. `atlas validate-router` 逐条调用当前 Router。
4. 结果写入 `router.validation` 审计事件。
5. `atlas health` 和 `atlas report` 读取最近一次评估指标。
6. 指标包含总数、intent accuracy、role accuracy、失败样本。
7. 验证集本身不包含答案内容，只评估路由分类。

### Router

Phase 4 首先引入确定性 Router，而不是先引入 LLM Router：

1. 输入是开发者问题。
2. Router 使用关键词规则和索引候选决定 intent 与目标角色。
3. 输出固定 schema：`intent`、`role`、`confidence`、`signals`、`recommended_tools`、`scope_candidates`、`warnings`。
4. Router 不直接读函数体，不越权执行修改。
5. 路由结果写入审计事件，便于后续统计误路由率。

四类 intent 映射如下：

| intent | 典型角色 | 推荐工具 |
| --- | --- | --- |
| `locate` | module / system | `atlas_search`, `atlas_symbols`, `atlas_file_context` |
| `impact` | architect / system | `atlas_references`, `atlas_impact` |
| `implementation` | developer | `atlas_scoped_search`, `atlas_file_context` |
| `memory` | module / system | `atlas_memories`, `atlas_file_context` |

Router 的置信度只表示「分类规则和索引信号是否一致」，不等于答案正确性。

### 影响分析过滤

`atlas_impact` 支持服务端 `min_confidence` 过滤：

1. `resolved` 默认置信度 0.95，`unique_name` 0.65。
2. `ambiguous` 和 `unresolved` 默认低于 0.65。
3. 调用方可以设置 `min_confidence=0.65` 排除噪声边。
4. 返回值同时给出 `filtered_count` 和 `references_by_resolution`，避免静默丢数据。

### 影响传播链

`atlas_impact_chain` 在 `atlas_impact` 之上增加两级传播：

1. 第一跳：目标符号的直接引用。
2. 第二跳：引用目标符号所在文件中的其他导出符号的上游文件。
3. 每跳都受 `min_confidence` 过滤。
4. 结果保留跳数、来源文件、目标文件、引用状态和置信度。
5. 当前链路基于 import/use 边，不假设真实函数调用图。

### 导出符号变更传播

索引器为导出符号建立变更历史：

| 状态 | 说明 |
| --- | --- |
| `added` | 修改/新增文件后首次出现的导出符号 |
| `removed` | 修改/删除/重命名前存在但之后消失的导出符号 |
| `unchanged` | 修改前后仍存在的导出符号；不写入变更历史 |

`atlas propagate` / `atlas_export_propagation` 读取最近的导出变更，并为每个符号生成下游引用报告。删除文件在索引删除前先记录 removed 导出符号，避免丢失传播信号。

### 端到端编排

`atlas run` 将确定性链路串成一个命令：

```
question
  -> Router
  -> system/module/developer context packet
  -> deterministic structural workers
  -> upward convergence
  -> final pipeline result
```

Worker 是结构索引驱动的确定性角色，不调用 LLM，不读取函数体，不修改代码。Pipeline 输出 route、contexts、worker findings 和 convergence。缺少索引候选时返回 warning，而不是编造证据。

### 增量守护

`atlas daemon` 是前台轮询循环，不是 OS daemon：

1. 每个周期执行一次 `index_project`。
2. 默认使用 Git 工作区候选和增量 hash 判断。
3. `--interval` 控制轮询间隔。
4. `--max-iterations` 用于测试和一次性运行。
5. Ctrl-C 正常退出，已有错误不中断后续周期。

### Deep Parser

默认解析后端是 `auto`：

1. 安装 `tree-sitter-language-pack` 时使用 tree-sitter 抽取 symbols。
2. imports / calls 仍由各语言的轻量规则抽取。
3. 未安装 tree-sitter 时自动退回内置解析器。
4. 配置 `parser_backend: tree-sitter` 强制启用；`builtin` 强制禁用。
5. 解析失败写入 index diagnostics，不阻断索引。

### Hybrid Search

SQLite FTS5 维护 `search_index`：

1. 索引 symbols、semantic memories、contracts、architecture docs。
2. `atlas_hybrid_search` 使用 BM25 和结构权重合成排序。
3. 结果返回 doc_type、score、path、repo、subsystem、freshness。
4. 索引完成后自动重建；记忆 / 契约 / 文档变化后增量重建。
5. 未支持 FTS5 的 SQLite 自动退回 LIKE 搜索。

### 发布与 PR

已应用补丁可以进入 Git 发布流程：

1. `atlas patch propose-publish` 创建 high-risk `create_pr` 审批。
2. 人类审批后 `atlas patch publish` 才允许执行。
3. 可创建分支、commit 已应用补丁、push 到 remote。
4. 若安装并登录 `gh`，可创建 Pull Request。
5. 分支、commit、push、PR URL 全部写入审计。

### 生产评估

`atlas evaluate` 提供冻结评估 harness：

| 类型 | 指标 |
| --- | --- |
| `semantic` | expected term precision / recall / F1 |
| `agents` | expected symbol hit rate、evidence coverage、finding confidence |
| `router` | intent accuracy、role accuracy |

评估集由项目提供，可指向真实仓库。结果写入 audit 并可用于进化的 accepted 判定。

### 变体提升与回滚

`strategy_rule` 变体进入完整审批闭环：

1. `atlas evolution propose-promotion` 创建 high-risk `promote_variant` 审批。
2. 人类审批后 `atlas evolution promote` 才写入 `strategy_rules`。
3. Router 在运行时读取 active strategy rules。
4. `atlas evolution rollback` 也要审批，删除规则并记录回滚。
5. rule source 记录 variant_key，便于精确回滚。

### 上下文包

Phase 4 将上下文包构建做成固定边界的服务端能力：

| 角色 | 工具 | 包内容 |
| --- | --- | --- |
| system | `atlas_system_context` | 子系统/仓库清单、文件与符号统计、跨仓引用边、置信度、警告 |
| module | `atlas_module_context` | 文件模块名、符号清单、出向 import、入向引用、相关文件 |
| developer | `atlas_developer_context` | 目标文件与目标符号的窄化上下文、入向影响、scope 候选；不含函数体 |

构建规则：

1. 所有上下文来自 SQLite 结构索引，不返回完整代码。
2. system 包默认聚合跨仓引用边，用于架构师判断影响传播。
3. module 包展示文件的符号边界和入向依赖。
4. developer 包只针对一个文件；指定 symbol 时进一步收窄到该符号。
5. developer 包返回 scope 候选，但不自动创建 scope token。
6. 任一层查询的文件/仓库不存在时返回显式错误，不返回空假成功。

### 四层 Agent

`atlas agents` 运行完整下行链路：

```
question
  -> Router
  -> architect agent
  -> system agent
  -> module agent
  -> scoped developer agent
  -> upward convergence
```

1. Architect agent 只看全局 repo/subsystem、cross-repo edge 和契约。
2. System agent 只看目标 repo 的文件/符号统计与部署边界。
3. Module agent 只看目标文件的符号、import、入向引用。
4. Developer agent 通过编排器创建服务端 scope token，读取物理文件片段。
5. `read_scoped_file` 先校验 scope，再读取文件；读取事件写入审计。
6. 可通过环境变量接入 OpenAI-compatible LLM；无配置时使用确定性 claim。
7. Kernel Budget 限制时间、调用次数和 token 数。
8. 每个 finding 通过 kernel evidence verifier；`private_score` 只返回分数。

### 上行回溯汇聚

`atlas_converge` 接收多个角色 Worker 的结构化发现，按上行顺序汇聚：

1. `developer` 返回目标实现发现。
2. `module` 叠加模块边界、测试位置、入向依赖。
3. `system` 叠加仓库、部署和同步注意事项。
4. `architect` 叠加跨子系统契约和传播影响。

输入 schema：

| 字段 | 说明 |
| --- | --- |
| `question` | 原始开发者问题 |
| `findings` | 角色发现数组，每项必须有 `role`、`claim`、`evidence` |
| `route` | 可选，传入 `atlas_route` 结果 |
| `impact_symbol` | 可选，触发影响传播链 |
| `min_confidence` | 可选，影响边过滤阈值 |

每个 finding 的 evidence 必须引用索引文件路径和正整数行号；Server 会校验文件路径存在于索引中。

输出固定包含 ordered findings、role summary、missing layers、cross-repo evidence、可选 impact propagation、answer sections 和 warnings。汇聚器不生成代码，不自动提交修改，也不把缺少证据的 finding 当作可执行结论。

### 语义记忆

`atlas memory generate` 生成 file / repo / subsystem 三层语义记忆：

1. 默认从 AST、symbols、imports 生成确定性 claim。
2. 配置 LLM 后可生成自然语言摘要；LLM 失败自动退回确定性结果。
3. 每条记忆写入 `semantic_generations`，记录 provider。
4. 证据绑定索引文件和行号。
5. `atlas memory auto-review` 对 stale 记忆读取证据文件，按 claim 关键词复核并写入 `memory_reviews`。
6. `atlas memory lessons` 从 Git revert 提交提取行为记忆。
7. utility score 可被检索使用和复核调整。

### 架构契约与知识源

`atlas architecture` 管理显式架构知识：

1. `sync` 从 config.contracts 同步契约。
2. `contracts` 列出 provider、version、consumers 和状态。
3. `impact` 将契约名映射到引用图，输出受影响 repo。
4. `docs-index` 索引 Markdown/JSON/YAML 架构文档，按 architecture/api/database/contract/adr 分类。
5. `docs` 返回标题、摘要、hash 和 indexed_at。

### Kernel

Kernel 是不可绕过的执行边界：

| 规则 | 实现 |
| --- | --- |
| 资源熔断 | `Budget` 限制 elapsed seconds、calls、tokens |
| 高危拦截 | `delete_file`、`external_send`、`permission_change`、`payment`、`runtime_self_modify`、`apply_patch` 必须审批 |
| 中风险 | `propose_patch`、`create_pr`、`memory_generate` 需要审批或记录 |
| 可验证性 | finding 的 evidence 文件必须存在于索引 |
| 私有评分 | `private_score` 只返回分数，不暴露公式 |
| 审计 | 请求、决策、补丁应用、Agent 运行写入 audit |

### 变更工作流

`atlas patch` 提供最小安全闭环：

1. `propose` 要求精确 old_text / new_text，生成 unified diff，并创建 high-risk approval。
2. `decide` 由人类 approver 接受或拒绝。
3. `apply` 只接受 approved patch，并验证目标 old_text 仍唯一。
4. 可选 `--test-command` 在本地沙盒命令中执行测试；失败则不写文件。
5. 应用写入 `patch.applied` 审计。

### 离线进化

`atlas evolution` 只在离线边界运行：

1. `distill` 从 router 审计生成 strategy rules 和 utility score。
2. `create` 只允许 router_terms、strategy_rule、context_template 三类变体。
3. `evaluate` 使用冻结 Router cases；变体不直接修改运行时规则。
4. accepted 表示可由人类提升到配置，不是立即自动替换。
5. 所有创建和评估写入审计。

### MCP

Server 支持 JSON-RPC over stdio（`atlas serve`）和 HTTP（`atlas serve-http`），均支持 `initialize`、`tools/list`、`tools/call`。工具返回结构化摘要，限制输出条数，避免一次性返回大段代码。HTTP 可用 `ATLAS_HTTP_TOKEN` 设置 Bearer token。

### Scope

1. `atlas_create_scope` 接收路径前缀列表。
2. Server 校验路径必须位于注册仓库内。
3. 生成随机 token，记录过期时间，默认 15 分钟。
4. `atlas_scoped_search` 必须携带 token。
5. 查询结果仅保留 token 范围内的路径。
6. token 记录 `used_at`，可继续审计使用历史。

### 记忆检索与复核

1. `atlas_memories` 按 `valid` / `stale` / `invalid` 列出记忆及其证据文件。
2. stale 记忆默认不会被静默混入普通索引答案。
3. `atlas_review_memory` 由外部审查者提交 `valid` 或 `invalid` 决策。
4. 复核结果写入 `memory_reviews`，同步更新记忆状态和 `updated_at`。
5. ATLAS 只保存决策与证据，不假装自动理解语义。

## 5. 边界与错误处理

1. 配置不存在或非法时，`init` / `index` / `serve` 立即失败并给出明确错误。
2. 解析失败的文件记录为无符号，不阻断整体索引。
3. 解析失败和无法解码的文件写入 `index_diagnostics`，并在 health 中返回计数。
4. 旧数据库在连接时自动迁移缺失列和新表；迁移只增加列、表和索引，不删除用户数据。
5. SQLite 使用 WAL，限制并发写冲突。
6. MCP 查询缺少参数时返回协议级错误。
7. scope token 不存在、过期、已撤销或范围不匹配时拒绝查询。
8. `min_confidence` 被限制在 0.0 到 1.0。
9. 默认最多返回 50 条结果，可由调用参数调整，但上限为 200。
10. 索引不尝试读取完整文件内容进入模型上下文。
11. developer 上下文包只提供结构和引用边界；访问完整源码仍必须通过外部工具和 scope token。
12. 汇聚输入中的 evidence 文件必须在索引中存在；不存在时返回显式错误。
13. Pipeline worker 只产生结构性发现；缺少索引候选时输出 warning，不编造证据。
14. Agent Budget 超限时终止运行并记录 failed。
15. HTTP Server 默认绑定 127.0.0.1；设置 `ATLAS_HTTP_TOKEN` 后未认证请求返回 401。

## 6. 安全与权限

1. 索引和查询默认只读。
2. 数据库、scope token 和审批状态保存在项目 `.atlas/` 目录内。
3. 代码修改必须经过 propose → human approve → apply，并且 apply 只接受 approved 状态。
4. scope token 是服务端校验对象，不能由客户端修改路径范围。
5. Developer Agent 读取物理文件必须携带服务端 scope token。
6. LLM API key 只从环境变量读取，不写入 config。
7. 记忆复核、审批、补丁应用、Agent 运行和 Router 评估写入审计。

## 7. 验收清单

### 基础

- [x] `atlas init` 创建 `.atlas/config.json` 和 SQLite 数据库。
- [x] `atlas index` 能扫描并注册配置仓库。
- [x] Python AST 符号抽取正确。
- [x] TypeScript/JavaScript 基础符号抽取正确。
- [x] `atlas health` 显示覆盖率。

### MCP

- [x] `initialize` 协议握手成功。
- [x] `tools/list` 返回工具清单。
- [x] `atlas_search` 返回符号摘要。
- [x] `atlas_references` 能返回引用边。
- [x] `atlas_impact` 返回下游影响位置。

### Scope

- [x] scope token 生成后可限制查询路径。
- [x] 过期 token 被拒绝。
- [x] 未授权路径结果被过滤。
- [x] scope 使用记录进入审计。

### 质量

- [x] pytest 覆盖索引、搜索、引用、scope 和 MCP 协议。

### Phase 2 工作区与健康报告

- [x] tracked 工作区修改 / 删除检测。
- [x] untracked 文件检测。
- [x] Git 增量候选合并策略。
- [x] Markdown / JSON 健康度报告。

### Phase 2 增量与语言

- [x] Go 基础符号与 import 抽取。
- [x] Java 基础符号与 import 抽取。
- [x] Rust 基础符号与 use/import 抽取。
- [x] Git-aware 提交间增量索引。
- [x] 非 Git 仓库自动降级全量索引。
- [x] 索引诊断写入数据库并在 health 中展示。
- [x] 引用边解析、置信度和跨仓标识。
- [x] 重复引用边去重。
- [x] schema 版本化与基础迁移。

### Phase 4 Router 与记忆复核

- [x] 确定性 Router 输出固定分类 schema。
- [x] `atlas_route` MCP 工具。
- [x] `atlas_impact` 支持 `min_confidence`。
- [x] `atlas_memories` 返回证据引用。
- [x] `atlas_review_memory` 保存复核决策。
- [x] `atlas_system_context` 构建系统层包。
- [x] `atlas_module_context` 构建模块层包。
- [x] `atlas_developer_context` 构建开发层包。
- [x] `atlas_impact_chain` 输出两级影响传播链。
- [x] `atlas_converge` 按上行顺序汇聚角色发现。
- [x] Router 冻结验证集为 20 条。
- [x] `atlas validate-router` 评估并记录 route accuracy。
- [x] `atlas_export_propagation` 报告导出符号下游引用。
- [x] `atlas run` 串联 Router / context / worker / convergence。
- [x] `atlas daemon` 支持前台轮询增量索引。

### Phase 5 Kernel / Agent / Memory / Evolution

- [x] Python / TS / JS / Go / Java / Rust 基础 call edge 抽取。
- [x] 并行文件解析。
- [x] Kernel Budget 时间 / 调用 / token 熔断。
- [x] 中高风险审批。
- [x] finding evidence verifier 和 private score。
- [x] 四层 Agent 与 scoped developer file read。
- [x] OpenAI-compatible LLM Provider。
- [x] 语义记忆生成与 stale 自动复核。
- [x] Git revert 教训提取。
- [x] 架构契约同步与契约影响。
- [x] 架构文档 / ADR 索引。
- [x] approved patch / sandbox test workflow。
- [x] 策略蒸馏与离线变体评分。
- [x] HTTP JSON-RPC Server。

## 8. 后续演进

1. 用 tree-sitter 或语言服务替换基础正则解析器。
2. 增加 embedding / hybrid search。
3. 扩展 PR / Git remote 集成。
4. 将文件监听从轮询升级为 OS event。
5. 为 LLM Worker 建立真实项目评估集。
6. 将 accepted variants 的人类提升流程自动化。

