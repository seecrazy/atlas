# Security Policy

## 支持范围

ATLAS 当前是本地单机 / 单工作区系统。以下组件在支持范围内：

1. CLI。
2. MCP stdio server。
3. MCP HTTP server。
4. SQLite schema / migration。
5. scope token。
6. patch / approval workflow。
7. Git / PR publishing。

## 不在当前支持范围

1. 多用户共享索引服务。
2. SSO / RBAC。
3. 跨机器分布式部署。
4. 远端索引同步。

## 报告漏洞

请不要在公开 issue 中提交可利用细节。

提供以下信息：

1. 受影响组件。
2. 复现步骤。
3. 最小化样本。
4. 预期行为。
5. 实际行为。
6. 影响评估。

维护者会在确认后跟进修复。

## 安全模型要点

1. LLM API key 只从环境变量读取。
2. `apply_patch` 必须经过人工审批。
3. test command 使用精确 allowlist。
4. scoped file read 拒绝 symlink 逃逸。
5. PR 发布使用临时 Git worktree。
6. 高危动作写入 audit。
