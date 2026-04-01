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
- 自动更新 README.md 记录变更

### mcp-deploy

MCP 服务器自动部署工具。

- 当用户说"部署 MCP"、"安装 MCP"、"配置 MCP"时自动激活
- 支持智谱 MCP、Minimax MCP、Gitea MCP 等平台的自动配置
- 提供常用 MCP 的配置参考和部署流程

### email

邮件处理工具（基于 IMAP）。搜索、读取、下载附件、移动邮件。

- 当用户需要查看、搜索、管理邮件、下载附件、移动归档邮件时自动激活
- 支持 IMAP 协议，提供完整的邮件管理能力
- 内置配置初始化工具，支持多账户管理
- 密码加密存储在本地 .env 文件中

### feishu-image

通过飞书 (Lark) API 发送图片和截图。

- 当用户要求"截图发给我"或发送图片到飞书时自动激活
- 支持在 OpenClaw 中自动读取飞书配置
- 支持独立使用（通过环境变量配置）
- 提供 CLI 工具和 Node.js SDK

### tencent-docs

腾讯文档 MCP，提供完整的腾讯文档操作能力。参考：[腾讯文档 MCP 使用指南](https://docs.qq.com/aio/p/scg9of08ycfdc59?p=SIBSMoo4XuO9dymUo2GEDBm)

- 当用户需要操作腾讯文档时自动激活
- 支持创建各类在线文档（智能文档、Word、Excel、幻灯片、思维导图、流程图）
- 支持查询、搜索文档空间与文件
- 支持读取和编辑智能文档、智能表格

### media-use

媒体处理工具集，基于 ffmpeg 提供视频转码等功能。

- 当用户需要进行视频转码、格式转换、音频处理等媒体操作时自动激活
- 包含 ffmpeg_batch 批量视频转码工具
- 支持多种视频编码（H.264、H.265/HEVC、AV1、VP9）
- 支持多种音频编码（AAC、MP3、Opus、FLAC、AC3）
- 支持 GPU 硬件加速（NVIDIA NVENC、Intel QSV、VAAPI）

### video-analyzer

使用视觉/视频大模型分析视频内容。

- 当用户说"分析视频"、"视频理解"、"看看这个视频"时自动激活
- 支持本地视频文件和互联网视频（直接 URL 及 YouTube/Bilibili 等站点）
- 支持抽帧分析和原生视频输入两种模式
- 多模型配置（豆包、GPT-4o 等 OpenAI 兼容 API）

### image-generation

AI 图片生成工具，通过 OpenAI/Gemini 兼容 API 生成图片。

- 当用户说"生成图片"、"画图"、"封面图"、"配图"、"AI生图"时自动激活
- 支持多 provider 配置（OpenAI 兼容 / Gemini 兼容）
- 支持从 API 动态获取可用模型列表
- 自动将中文 prompt 优化为英文，提升生成效果

### joplin

用于调用 Joplin REST API 读写笔记、查询笔记本、搜索内容等。

- 当用户需要查看、搜索、保存内容到 Joplin 时自动激活
- 支持通过命令行读写 Joplin 笔记（需要开启 Joplin Clipper 服务）
- 支持笔记搜索、内容获取、创建、列出笔记本等操作
- 自动读取 `.env` 中的 API Token

### vikunja

Vikunja 任务管理工具，将已完成任务同步到 Joplin weekly 笔记。

- 当用户需要查看 Vikunja 项目/任务、同步任务到 Joplin 时自动激活
- 支持列出项目和任务（按项目、完成状态、周过滤）
- 支持将指定周的已完成任务同步到 Joplin GTD 笔记本的 weekly 笔记
- 自动去重，带 `[x]` 标记已完成任务

## 更新记录

### 2026-04-01
- 新增 vikunja skill：Vikunja 任务管理工具，支持查看项目和任务，并将每周已完成的任务同步到 Joplin weekly 笔记

### 2026-03-29
- 新增 image-generation skill：AI 图片生成工具，支持多 provider（OpenAI/Gemini 兼容 API），支持模型列表查询和 prompt 优化

### 2026-03-28
- 升级 tencent-docs skill 到 v1.0.21：同步官方压缩包内容，新增 smartcanvas/diagram/slide/docengine/workflows 等参考文档，更新入口说明与版本元数据
- 精简 git-commit skill：收紧触发描述，统一 CalVer/tag 工作流表述，并明确脚本定位规则

### 2026-03-21
- 新增 CLAUDE.md：为 Claude Code 创建项目规范执行手册，与 GEMINI.md 保持一致
- 将所有 skills 通过符号链接注册到 ~/.claude/skills/，实现全局可用

### 2026-03-19
- 新增 joplin skill：用于通过 REST API 调用本地 Joplin 笔记库，支持笔记读写、搜索和笔记本管理

### 2026-03-17
- 新增 email skill：邮件处理工具（基于 IMAP），支持搜索、读取、下载附件和移动邮件
- 升级 tencent-docs skill v1.0.13：重构 SKILL.md，新增 doc/（文档编辑与格式化）、sheet/（表格操作与 JS 脚本）模块，补充认证与管理 API 参考文档，增强 setup.sh
- 升级 git-commit skill：重构工作流，使用 README.md 代替 CLAUDE.md 记录更新记录，并启用 GEMINI.md 作为规范说明文档

### 2026-03-14
- 新增 media-use skill：媒体处理工具集，包含 ffmpeg_batch 批量视频转码工具
- 更新 README.md：添加 media-use skill 说明

### 2026-03-11
- 新增 tencent-docs skill：腾讯文档 MCP，提供完整的腾讯文档操作能力（创建、编辑、搜索文档）
