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

### media-use

媒体处理工具集，基于 ffmpeg 提供视频转码、裁剪、合并、修复等功能。

- 当用户需要进行视频转码、格式转换、音频处理、视频裁剪/剪辑、视频合并、修复 m3u 下载的损坏视频时自动激活
- ffmpeg_batch：批量视频转码（H.264、H.265/HEVC、AV1、VP9，支持 GPU 硬件加速）
- ffmpeg_cut：按起止时间无损裁剪视频片段（-c copy，支持 HH:MM:SS.ms / 秒数）
- ffmpeg_merge：合并编码一致的视频文件（concat demuxer + -c copy，自动一致性校验）
- ffmpeg_fix：修复 m3u 下载的 mp4（faststart，moov atom 前置，支持文件 / 文件夹批量）
- 支持 AAC、MP3、Opus、FLAC、AC3 等音频编码

### video-downloader

统一视频下载工具，整合 `douyin-downloader`、`wx_channels_download` 与 `yt-dlp` 三种 backend。

- 当用户发来视频链接并要求下载，或提到 `yt-dlp` / `douyin-downloader` / 微信视频号 / 抖音短链时自动激活
- 支持抖音、微信视频号 SPH 分享链接，以及 yt-dlp 支持的网站视频下载
- 支持抖音热搜榜、搜索作品、刷新 Cookie
- 视频号认证失效时使用专用浏览器自动刷新腾讯元宝 Cookie，下载结果按视频号昵称归档
- `douyin-downloader` 已集成到 `.runtime/douyin-downloader`，无需外部安装

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

### immich

将图像和视频上传到 Immich 服务器。

- 当用户说"上传到 Immich"、"上传图片"、"备份照片"、"上传视频"、"下载视频并上传 Immich"时自动激活
- 只负责本地文件上传；网络资源先由 video-downloader 下载，再将本地路径交给 immich
- 支持批量上传、Album 管理、完整描述写入，并默认按本次上传时间排列资源
- 提供 Python SDK 和 CLI 工具

### jellyfin

Jellyfin 媒体库文件命名工具。按 Jellyfin 标准批量重命名电影/剧集文件夹和文件。

- 当用户需要整理 Jellyfin 媒体库、重命名媒体文件夹、获取 IMDB ID 时自动激活
- 支持解析 BT/字幕组命名风格（点分隔、中英混合、质量标记如 `1080P.X264.AAC`）
- `clean`：批量去除文件名中的空格，图片重命名为 poster 格式
- `rename`：查询 OMDb API 获取 IMDB ID，重命名为 `Movie (year) [imdbid-ttXXXX]` 格式，支持电影和剧集

### vikunja

Vikunja 任务管理工具，将已完成任务同步到 Joplin weekly 笔记。

- 当用户需要查看 Vikunja 项目/任务、同步任务到 Joplin 时自动激活
- 支持列出项目和任务（按项目、完成状态、周过滤）
- 支持将指定周的已完成任务同步到 Joplin GTD 笔记本的 weekly 笔记
- 自动去重，带 `[x]` 标记已完成任务

### spritesheet

从视频中提取帧生成 spritesheet 和独立透明 PNG。

- 当用户说"制作 spritesheet"、"视频转精灵图"、"提取动画帧"、"sprite sheet"、"循环动画"时自动激活
- 支持自动检测背景色（绿幕/蓝幕/白幕/黑幕）并抠图
- 默认主体感知全局接缝检测（主体 mask 内全局搜索最优循环接缝），支持 `--analyze` 诊断与 `--repack-dir` 删帧重打包
- 输出独立透明 PNG、合并 spritesheet 和可交互的动画播放器

### ui-extractor

从静态图片中提取前景元素并分离 UI 组件。

- 当用户说"提取 UI 元素"、"分离组件"、"去除背景"、"棋盘格背景"、"绿幕抠图"、"checkerboard 抠图"时自动激活
- 支持棋盘格背景去除（自动检测 + 透视校正）和关键色抠图（绿/蓝/白/黑）
- chroma 算法直接复用 spritesheet skill 的核心（保持一致性）
- 输出透明背景 PNG、分离的 UI 元素 PNG、元数据 JSON

## 共享开发资源

- [`shared/agent-config`](shared/agent-config/README.md)：`agent_config.toml` 的跨平台查找、全局兜底、显式路径、配置示例和测试模板。创建新 skill 时复制实现，运行时不依赖仓库共享目录。

## 更新记录

### 2026-07-16
- 统一 video-downloader 输出目录：yt-dlp 下载默认按作者名或账号名建立子目录，与抖音、微信视频号保持一致；作者字段缺失时使用 `unknown-author`

### 2026-07-15
- 新增 `shared/agent-config`：统一 `agent_config.toml` 的四级查找顺序、显式路径、软硬缺失策略、配置示例及接入文档，供新 skill 复制复用
- 优化微信视频号下载命名：文件名保留中文原始标题，移除话题、非法字符和符号字符，标题限制 30 字符；下载结果同时输出完整原始描述供 Immich 保存
- 升级 immich skill：新增 `asset_time_source` 和 `--asset-time`，默认在元数据提取后将时间线时间修正为本次上传时间；单文件上传支持 `--description` 保存完整标题与话题

### 2026-07-14
- 升级 video-downloader skill：新增微信视频号 SPH 分享链接下载，通过本地 `wx_channels_download` API 解析；认证缺失或过期时使用专用 Playwright 浏览器自动刷新腾讯元宝 Cookie、重启服务并重试，下载文件按视频号昵称建立目录，兼容迁移旧根目录文件
- 拆分下载与上传职责：immich 移除 `upload-url`、`yt-dlp` 和未使用的 `aiohttp` 依赖，只处理本地资源；网络下载统一由 video-downloader 完成并通过本地文件路径衔接
- 升级 immich skill：为默认公开相册增加 `public_album_url` 配置，资源成功加入相册后返回并显示可匿名访问的资源链接；覆盖本地、批量和重复资源上传场景

### 2026-07-12
- 修复 immich skill：配置查找新增 `~/.agents/agent_config.toml` 最终兜底；统一约定 `base_url` 不含 `/api` 并由客户端自动追加，兼容旧配置且避免生成 `/api/api/assets`
- 修复 video-downloader 的 `douyin_downloader_home` 配置，使检测、安装和运行统一使用该目录；路径支持 `~`、环境变量及相对 skill 目录的写法，并移除代码和说明中的机器相关绝对路径

### 2026-07-04
- 升级 media-use `ffmpeg_cut`：`-s/--start` 与 `-e/--end` 改为可选（二者至少传一个），省略 `-s` 表示从开头开始，省略 `-e` 表示裁剪到末尾，支持单端裁剪场景

### 2026-07-04
- 维护：从 git 索引移除 `.serena/`（`.gitignore` 早已有 `.serena/` 规则，但因 `.serena/.gitignore`、`.serena/project.yml` 仍被跟踪而失效），本地文件保留，后续 `.serena/` 修改不再进入版本控制
- 修复 media-use `common.py`：ffmpeg 调用显式传入 `-y`，并新增对「Not overwriting / Error opening output file」的防御性检查，规避 ffmpeg 8.x 在非交互 stdin 下退出码为 0 却未写出文件的隐患

### 2026-07-04
- 升级 media-use skill：重构为统一 `media_use` 包结构，新增 3 个 ffmpeg 工具
  - 新增 `ffmpeg_cut`：按起止时间无损裁剪视频片段（-c copy，支持 HH:MM:SS.ms / MM:SS / 秒数）
  - 新增 `ffmpeg_merge`：合并编码一致的视频文件（concat demuxer + -c copy，ffprobe 自动一致性校验）
  - 新增 `ffmpeg_fix`：修复 m3u 下载的 mp4（-c copy -movflags +faststart，支持文件 / 文件夹批量）
  - `ffmpeg_batch` 迁移至 `media_use/convert.py`，命令行接口与行为完全不变
  - 公共逻辑下沉到 `media_use/common.py`（ffprobe 探测、ffmpeg 封装、时间解析、目录安全检查）

### 2026-07-01
- 升级 video-downloader skill：整合 douyin-downloader 到内部（`.runtime/douyin-downloader`），新增 refresh-cookies、hot-board、search 命令
- 移除 tencent-docs skill（不再维护）

### 2026-06-16
- 重构 spritesheet skill 抠图与裁剪流程：统一步骤编号，删除已废弃的 `--canvas-size` 参数
- 新增抠图与裁剪流程参考文档 `references/pipeline.md`，详细记录 9 步流水线和抠图算法实现
- 集成循环分析能力：新增主体感知全局接缝检测 `find_loop_point_global` 作为默认循环检测，突破旧 CV 法"循环从第 0 帧开始"与"背景稀释 MSE"两个局限
- 新增 `--analyze` 诊断模式（输出 MSE 曲线/周期候选/质心轨迹/主体大小趋势报告）与 `--repack-dir` 删帧重打包模式
- 代码拆分为 5 个子模块（`chroma`/`subject`/`loopdetect`/`analyze`/`repack`），旧 CV 帧差法保留为 `--from-frame-zero`
- 新增 ui-extractor skill：从静态图片中提取 UI 元素，支持棋盘格 + chroma 两种背景去除，可选透视校正，复制 spritesheet 的 chroma 算法保持一致
- 职责分离：spritesheet 移除 `--smart`（大模型循环分析），该能力迁至 video-analyzer——video-analyzer 新增 `--json` 结构化输出（自动附视频帧数/帧率/时长），循环分析 prompt 模板见 `video-analyzer/references/loop-analysis.md`

### 2026-06-13
- 重构 spritesheet skill `--smart` 模式：根据 API 端点自动选择视频传入方式
  - 标准端点 `/api/v3`: 使用 `input_video` 直接传入视频（base64），平台按 fps=5 自动抽帧
  - Coding plan `/api/coding/v3`: 使用 `input_image` 手动抽帧以图片方式传入
  - 添加完整 API 文档注释（火山方舟 Responses API / Chat API）

### 2026-06-11
- 优化 spritesheet skill：改进 AI 循环区间分析提示词，增加视频元数据（总帧数、帧率、时长），添加运动描述字段，提升动画质量

### 2026-06-10
- 新增 spritesheet skill：从视频中提取帧生成 spritesheet 和独立透明 PNG，支持自动抠图、AI 智能分析循环区间
- 升级 video-analyzer skill：新增 `agent_config.toml` 多 skill 共用配置支持，支持直接填写 API Key，提供四位置配置文件发现策略
- 更新 `.gitignore`：新增 `temp/` 目录忽略

### 2026-05-21
- 升级 git-commit skill：
  - 新增 Commit Message 规范，强制使用 Conventional Commits 格式并附加 AI 元数据（`Agent-Task`, `Agent-Model`, `Agent-Decision`, `Agent-Limitation`）作为 Git Trailers。
  - 新增原子提交（Atomic Commits）规范与工作流，规范化逻辑独立的提交拆分。
  - 新增“整理提交历史/PR准备”工作流，支持交互式变基（rebase/squash）的方案预览与用户确认流程。
- 更新 `.gitignore`：加入 `.antigravitycli/` 目录以忽略本地 CLI 配置与运行缓存。

### 2026-05-20
- 新增 video-downloader skill：统一封装 `douyin-downloader` 与 `yt-dlp`，支持项目级/全局 `agent_config.toml` 配置回退，保留 `douyin-downloader` 原生 `config.yml` 作为底层运行配置
- 更新 `.gitignore`：忽略项目级 `agent_config.toml` 与 `video-downloader/.runtime/`

### 2026-05-01
- 新增 jellyfin skill：Jellyfin 媒体库文件命名工具，支持解析 BT/字幕组命名风格，查询 OMDb API 获取 IMDB ID，按 Jellyfin 标准重命名电影和剧集文件夹及内部文件

### 2026-04-03
- 新增 immich skill：将图像和视频上传到 Immich 服务器，支持本地文件上传、远程 URL 下载上传、批量上传和 Album 管理

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
