# Contributing

感谢参与 ATLAS。为了让项目保持可维护和安全，请遵循以下流程。

## 开发环境

```bash
python3 -m pip install -e '.[production]'
python3 -m pytest -q
```

最低要求 Python 3.9。

## 分支

建议使用短生命周期分支：

```text
feature/atlas-xxx
fix/atlas-xxx
docs/atlas-xxx
```

## 提交前检查

```bash
python3 -m pytest -q
git diff --check
PYTHONPATH=src python3 -m atlas.cli doctor .
```

如果修改了目标项目索引行为，还需要：

```bash
PYTHONPATH=src python3 -m atlas.cli index . --full
PYTHONPATH=src python3 -m atlas.cli evaluate . --no-llm
```

## 测试要求

1. 新增 CLI 参数必须有测试。
2. 新增 MCP 工具必须有 schema 和 dispatch 测试。
3. 新增索引能力必须有 golden fixture 或行为测试。
4. 安全相关行为必须有 negative test。
5. Bug fix 必须先补回归测试。

## 文档要求

| 变更类型 | 必须更新 |
| --- | --- |
| 用户可见命令 | `README.md` |
| 架构 / 数据模型 | `docs/design.md` |
| 完成状态 | `docs/roadmap.md` |
| 交接上下文 | `docs/HANDOFF.md` |
| 设计取舍 | `docs/adr/` |
| 发布行为 | `CHANGELOG.md` |

## 代码风格

1. Python 3.9 兼容。
2. 优先标准库，避免引入重量级运行时依赖。
3. 公开函数需要类型注解。
4. SQLite schema 变更必须保持向后兼容迁移。
5. 不把 secret 写入配置文件或日志。

## 安全

不要在 issue / PR 中提交 token、内网地址或客户代码。安全问题请通过私有渠道联系维护者，不要公开披露。
