# ATLAS 自项目生产评估

## 概述

本次评估将 ATLAS 自身作为被测项目，验证完整本地 MVP 在真实 Python 项目上的端到端行为。评估未配置 LLM，因此四层 Agent 使用确定性 Worker；解析后端使用 tree-sitter；搜索使用 SQLite FTS5。

## 被测项目

| 项目 | ATLAS |
| --- | --- |
| 索引文件 | 31 |
| Python 文件 | 31 |
| 符号 | 247 |
| 引用边 | 3,268 |
| import/use 边 | 406 |
| call edge | 2,908 |
| 索引诊断错误 | 0 |
| 语义记忆 | 33 |
| 架构文档 | 0 |
| 契约 | 0 |

## 结果

### Router

| 指标 | 值 |
| --- | --- |
| 用例数 | 20 |
| intent accuracy | 1.0000 |
| role 评估数 | 16 |
| role accuracy | 1.0000 |

### 四层 Agent

| 指标 | 值 |
| --- | --- |
| 用例数 | 3 |
| hit rate | 1.0000 |
| evidence coverage | 1.0000 |
| average confidence | 0.7425 |

三个用例分别命中：

1. `run_layered_agents`
2. `rebuild_search_index`
3. `route_request`

每个用例都完成了架构师 → 系统 → 模块 → scoped developer 链路，并产生可汇聚 finding。

### 语义记忆

| 指标 | 值 |
| --- | --- |
| 用例数 | 3 |
| precision | 0.4778 |
| recall | 0.9524 |
| F1 | 0.6126 |

召回率很高，但 precision 受确定性 claim 模板影响：claim 包含符号数量、路径、导出状态等结构性字段，评估分词会把模板词计入 produced terms。后续接入 LLM 或细化模板可以提升 precision。

## 复现

```bash
PYTHONPATH=src python3 -m atlas.cli index . --full
PYTHONPATH=src python3 -m atlas.cli validate-router .
PYTHONPATH=src python3 -m atlas.cli memory generate . --no-llm
PYTHONPATH=src python3 -m atlas.cli evaluate . --no-llm \
  --cases docs/evaluation/production_cases.json
```

## 结论

ATLAS 已经可以在真实 Python 项目上完成：

1. tree-sitter 深度解析。
2. 结构、语义、契约和文档混合索引。
3. Router 精确路由。
4. 四层 Agent scoped 会话。
5. 证据校验和汇聚。

剩余工作不是功能缺口，而是质量提升：

1. 在更大多语言仓库上扩展评估集。
2. 配置真实 LLM Provider 后评估 Worker 输出质量。
3. 细化语义记忆模板或接入模型摘要以提升 precision。
4. 在多仓项目中验证契约影响和 PR 发布流程。
