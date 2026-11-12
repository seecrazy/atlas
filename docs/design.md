# ATLAS 设计方案

## 1. 目标

### 目标

1. 将超大项目结构组织为「子系统 → 仓库 → 模块 → 文件/符号」四层。
2. 通过本地 SQLite 和 MCP 工具提供低延迟、可验证的结构查询能力。
3. 支持多仓注册、跨仓引用、影响分析和基础记忆漂移。
4. 使用服务端短期 scope token 控制子任务访问边界。
5. 所有索引查询、scope 授权和记忆状态变化留下审计记录。

### 非目标

以下内容不在当前垂直切片中实现：

1. 四层角色 Agent 的完整调度闭环。
2. LLM 全量语义摘要。
3. 直接修改代码、创建 PR 或执行构建。
4. 运行时自我修改或进化闭环。
5. 权限变更、删除、支付等高危操作。

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
  "exclude": ["node_modules", "dist", "target", ".venv"]
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
| `scope_tokens` | 短期 token、路径边界、状态、过期时间 |
| `audit_events` | 工具调用、scope 创建、记忆漂移等审计事件 |

### 状态

| 对象 | 状态 |
| --- | --- |
| 文件 | `valid` / `changed` |
| 引用 | `resolved` / `unique_name` / `ambiguous` / `unresolved` |
| 诊断 | `warning` / `error` |
| 记忆 | `valid` / `stale` / `invalid` |
| scope token | `active` / `used` / `expired` / `revoked` |

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
6. 非 Git 仓库或 Git 命令失败时降级为全量扫描，保证可用性。
7. `full=True` CLI 参数可以强制全量重建候选集合。

当前增量索引只覆盖提交间变化，不承诺处理所有未提交工作区状态。未提交变更可以直接使用 `atlas index --full` 重建。

### MCP

Server 实现为 JSON-RPC over stdio，支持 `initialize`、`tools/list`、`tools/call`。工具返回结构化摘要，限制输出条数，避免一次性返回大段代码。

### Scope

1. `atlas_create_scope` 接收路径前缀列表。
2. Server 校验路径必须位于注册仓库内。
3. 生成随机 token，记录过期时间，默认 15 分钟。
4. `atlas_scoped_search` 必须携带 token。
5. 查询结果仅保留 token 范围内的路径。
6. token 记录 `used_at`，可继续审计使用历史。

## 5. 边界与错误处理

1. 配置不存在或非法时，`init` / `index` / `serve` 立即失败并给出明确错误。
2. 解析失败的文件记录为无符号，不阻断整体索引。
3. 解析失败和无法解码的文件写入 `index_diagnostics`，并在 health 中返回计数。
4. 旧数据库在连接时自动迁移缺失列；迁移只增加列和索引，不删除用户数据。
5. SQLite 使用 WAL，限制并发写冲突。
6. MCP 查询缺少参数时返回协议级错误。
7. scope token 不存在、过期、已撤销或范围不匹配时拒绝查询。
8. 默认最多返回 50 条结果，可由调用参数调整，但上限为 200。
9. 索引不尝试读取完整文件内容进入模型上下文。

## 6. 安全与权限

1. 索引是只读操作，不修改目标项目文件。
2. 数据库和 scope token 保存在项目 `.atlas/` 目录内。
3. 高危操作当前不存在。
4. scope token 是服务端校验对象，不能由客户端修改路径范围。
5. 所有 scope 创建和使用事件写入审计表。

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

## 8. 后续演进

1. 增加更多语言解析器：Go、Java、Rust。
2. 引入 tree-sitter 或稳定语言服务作为后续索引后端。
3. 为热点文件增加按需语义摘要。
4. 增加 Router / Worker 编排器原型。
5. 增加 Git 事件守护进程与未提交变更监听。
6. 将 `create_scope` 从通用 MCP 工具迁出，改为编排器专用端点。
7. 增加冻结验证集，用于评估索引准确率和路由质量。

