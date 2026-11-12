# ATLAS

ATLAS 是一个面向超大项目的分层项目知识系统。它把项目组织为「子系统 → 仓库 → 模块 → 文件/符号」，通过本地索引、项目记忆和 MCP 工具回答定位、引用、影响范围和上下文检索类问题。

## 当前状态

项目处于 **Phase 1 垂直切片**：单仓/多仓基础索引、Python/TypeScript/JavaScript 基础符号抽取、SQLite 存储、本地 MCP stdio Server、基础 scope token 和审计记录已经可用。

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

# 启动 MCP stdio server
atlas serve /path/to/project
```

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
| `atlas_create_scope` | 创建短期受限访问 token |
| `atlas_scoped_search` | 携带 scope token 在授权范围内查询 |

`atlas_create_scope` 当前用于编排层能力验证。生产化前会改为只有受信任的编排器可调用，并增加撤销、频率限制和完整审计策略。

## 文档

- [设计方案](docs/design.md)
- [路线图](docs/roadmap.md)
- [架构决策记录](docs/adr/0001-local-sqlite-and-mcp.md)
- [交接记录](docs/HANDOFF.md)

## 开发

```bash
cd atlas
python3 -m pytest
```

