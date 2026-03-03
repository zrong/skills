---
name: mcp-deploy
description: MCP 服务器自动部署工具。当用户说"部署 MCP"、"安装 MCP"、"配置 MCP"时使用。支持智谱 MCP、Minimax MCP、Gitea MCP 等平台的自动配置。
---

# MCP Deploy

自动部署和配置各种 MCP 服务器。

## 支持的 MCP 平台

| 平台 | 类型 | 需要的配置 |
|------|------|------------|
| 智谱 (zhipu) | HTTP | API Key |
| 智谱搜索 (web-search-prime) | HTTP | API Key |
| 智谱网页读取 (web-reader) | HTTP | API Key |
| 智谱 GitHub (zread) | HTTP | API Key |
| Minimax | STDIO | API Key |
| Gitea | STDIO | API URL + Token |
| 其他 MCP | STDIO/HTTP | 根据具体情况 |

## 工作流程

1. **确认 MCP 平台**：询问用户要部署哪个平台的 MCP
2. **收集配置信息**：根据平台询问必要的配置（如 API Key、URL 等）
3. **执行配置**：使用 mcporter 命令添加 MCP 服务器
4. **验证部署**：测试 MCP 服务器是否正常工作
5. **报告结果**：展示部署结果和可用工具

## 常用配置参考

### 智谱 MCP (HTTP)

```bash
# 视觉理解
mcporter config add zhipu --url "https://open.bigmodel.cn/api/mcp/vision/mcp" --header "Authorization=Bearer YOUR_API_KEY"

# 联网搜索
mcporter config add web-search-prime --url "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp" --header "Authorization=Bearer YOUR_API_KEY"

# 网页读取
mcporter config add web-reader --url "https://open.bigmodel.cn/api/mcp/web_reader/mcp" --header "Authorization=Bearer YOUR_API_KEY"

# GitHub 仓库
mcporter config add zread --url "https://open.bigmodel.cn/api/mcp/zread/mcp" --header "Authorization=Bearer YOUR_API_KEY"
```

### Minimax MCP (STDIO)

```bash
# 需要先安装 uvx
which uvx || curl -LsSf https://astral.sh/uv/install.sh | sh

mcporter config add minimax --command "uvx" --args "minimax-coding-plan-mcp" --env "MINIMAX_API_KEY=YOUR_KEY,MINIMAX_API_HOST=https://api.minimaxi.com"
```

### Gitea MCP (STDIO)

```bash
# 需要创建 .env 文件包含 token
mcporter config add gitea --command "PATH_TO_RUN_SCRIPT" --env "VAR1=VALUE1"
```

## 配置文件位置

- MCP 配置：`~/.openclaw/workspace/config/mcporter.json`
- Gitea Token：`~/.openclaw/workspace/.gitea-mcp/.env`

## 注意事项

- 如果用户没有提供必要的配置信息，先询问
- 部署前检查目标 MCP 是否需要特殊的依赖（如 uvx）
- 部署后务必验证 MCP 是否正常工作
- 如果 MCP 离线，尝试诊断问题（网络、内网、依赖等）
