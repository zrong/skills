# zrong/skills

我的 AI Agent Skills 集合。

## 安装

```bash
# 安装所有 skills（全局）
npx skills add zrong/skills -g

# 安装到特定 agent
npx skills add zrong/skills --agent claude-code cursor openclaw

# 只安装特定 skill
npx skills add zrong/skills --skill git-commit
```

## Skills

### git-commit

Git 提交并打 CalVer 标签。

- 当用户说"提交"、"commit"、"打标签"、"tag"、"发版"时自动激活
- 自动计算 CalVer 版本号（YY.WW.MICRO 格式）
- 自动更新 CLAUDE.md 记录变更

### mcp-deploy

MCP 服务器自动部署工具。

- 当用户说"部署 MCP"、"安装 MCP"、"配置 MCP"时自动激活
- 支持智谱 MCP、Minimax MCP、Gitea MCP 等平台的自动配置
- 提供常用 MCP 的配置参考和部署流程

### feishu-image

通过飞书 (Lark) API 发送图片和截图。

- 当用户要求"截图发给我"或发送图片到飞书时自动激活
- 支持在 OpenClaw 中自动读取飞书配置
- 支持独立使用（通过环境变量配置）
- 提供 CLI 工具和 Node.js SDK

### video-analyzer

使用视觉/视频大模型分析视频内容。

- 当用户说"分析视频"、"视频理解"、"看看这个视频"时自动激活
- 支持本地视频文件和互联网视频（直接 URL 及 YouTube/Bilibili 等站点）
- 支持抽帧分析和原生视频输入两种模式
- 多模型配置（豆包、GPT-4o 等 OpenAI 兼容 API）

## 相关链接

- [skills.sh](https://skills.sh) - Skill 标准和发现平台
- [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) - 官方 skill 集合
