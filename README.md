# ATLAS

ATLAS 是一个面向超大项目的分层项目知识系统。它把项目组织为「子系统 → 仓库 → 模块 → 文件/符号」，通过本地索引、项目记忆和 MCP 工具回答定位、引用、影响范围和上下文检索类问题。

## 当前状态

项目处于 **Phase 4 确定性路由切片**：多仓基础索引、Python/TypeScript/JavaScript/Go/Java/Rust 基础符号抽取、SQLite 存储、本地 MCP stdio Server、Git-aware 增量索引、引用解析置信度、确定性 Router、记忆复核闭环、索引诊断、基础 scope token 和审计记录已经可用。

运行环境要求 Python 3.9+。

当前不包含：

- 四层角色 Agent 的完整路由闭环
- LLM 生成的语义摘要
- 代码变更执行和 PR 创建
- 自动进化层

## 快速开始

```bash
cd atlas
python3 -m pip install -e .

# 在任意项目根目录执行
atlas init /path/to/project
atlas index /path/to/project
atlas health /path/to/project

# 强制全量重建（Git 增量索引跳过时使用）
atlas index /path/to/project --full

# 启动 MCP stdio server
atlas serve /path/to/project
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
| `atlas_memories` | 查询记忆及证据状态 |
| `atlas_review_memory` | 提交记忆复核决策 |
| `atlas_create_scope` | 创建短期受限访问 token |
| `atlas_scoped_search` | 携带 scope token 在授权范围内查询 |

`atlas_create_scope` 当前用于编排层能力验证。生产化前会改为只有受信任的编排器可调用，并增加撤销、频率限制和完整审计策略。

## 文档

- [设计方案](docs/design.md)
- [路线图](docs/roadmap.md)
- [架构决策记录](docs/adr/0001-local-sqlite-and-mcp.md)
- [确定性 Router ADR](docs/adr/0002-deterministic-router-first.md)
- [交接记录](docs/HANDOFF.md)

## 开发

```bash
cd atlas
python3 -m pytest
```

