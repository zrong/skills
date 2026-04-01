---
name: vikunja
description: Vikunja 任务管理工具，支持查看项目和任务，并将每周已完成的任务同步到 Joplin weekly 笔记。
---

# Vikunja Skill

从自托管 Vikunja 实例读取任务，同步到 Joplin。

## 前置依赖

- **joplin skill** 必须已安装（`https://github.com/zrong/skills`）
- Vikunja 实例需要可访问
- 在 `agent_config.toml` 中配置 `[vikunja]` section

## 环境配置

在 `agent_config.toml` 中添加：

```toml
[vikunja]
api_url = "https://nas.zengrong.net:8456/api/v1"
api_token = "<your-api-token>"
```

## 命令指南

建议使用 `uv run --project vikunja/scripts vikunja/scripts/vikunja_tool.py <command>` 执行。

### 1. 项目

- **列出项目**: `list-projects`

### 2. 任务

- **列出任务**: `list-tasks [--project ID] [--done] [--week YYYY-Www] [--limit N]`

### 3. 同步

- **同步到 Joplin**: `sync-weekly [--note "2026-03-14weeks"] [--week "2026-W14"]`
  - 无参数：同步当前周
  - `--note`：从笔记标题解析目标周
  - `--week`：直接指定 ISO 周

## 测试流程

1. `uv run --project vikunja/scripts vikunja/scripts/vikunja_tool.py list-projects`
2. `uv run --project vikunja/scripts vikunja/scripts/vikunja_tool.py list-tasks --done --limit 5`
3. `uv run --project vikunja/scripts vikunja/scripts/vikunja_tool.py sync-weekly`
