---
name: jellyfin
description: Jellyfin 媒体库文件命名工具。当用户需要按照 Jellyfin 命名规范重命名电影/剧集文件夹和文件、获取 IMDB ID、处理 BT/字幕组风格的媒体文件名（含点分隔、中英混合、质量标记如 1080P.X264.AAC）时使用。支持 clean（批量去除空格）和 rename（智能重命名 + IMDB ID 查询，支持电影和剧集）。
---

# Jellyfin 媒体库重命名工具

## 配置

在 `agent_config.toml` 中添加 OMDb 配置（从 https://www.omdbapi.com/apikey.aspx 免费申请 API Key）：

```toml
[jellyfin.omdb]
base_url = "http://www.omdbapi.com"
api_key = "your_api_key_here"
```

配置文件搜索顺序：当前目录 → skill 目录 → git 根目录。

## 执行方式

```bash
# 变量定义（SKILL_DIR 为本 skill 的绝对路径）
SKILL_DIR="/path/to/skills/jellyfin"
uv run --project "$SKILL_DIR/scripts" "$SKILL_DIR/scripts/jellyfin_tool.py" <command> [options]
```

## 命令

### clean — 批量清理空格

遍历指定目录下的所有子文件夹，去掉文件夹名和文件名中的空格，图片重命名为 `{视频名}-poster{ext}`。

```bash
# 预览（不执行）
uv run --project "$SKILL_DIR/scripts" "$SKILL_DIR/scripts/jellyfin_tool.py" clean /path/to/media --dry-run

# 执行（跳过确认，适合 agent 使用）
uv run --project "$SKILL_DIR/scripts" "$SKILL_DIR/scripts/jellyfin_tool.py" clean /path/to/media --yes
```

### rename — 智能重命名（Jellyfin 规范 + IMDB ID）

解析 BT/字幕组风格的文件夹名，查询 OMDb API 获取 IMDB ID，按 Jellyfin 标准重命名。

**输出格式**：
- 电影文件夹：`Movie Name (year) [imdbid-ttXXXXXXXX]`
- 电影视频文件：`Movie Name (year) [imdbid-ttXXXXXXXX].mkv`
- 剧集文件夹：`Show Name (year) [imdbid-ttXXXXXXXX]`
- 剧集视频文件：`Show Name S01E01.mkv`
- 图片：首图 `Name (year) [imdbid-ttXXXXXXXX]-poster.jpg`，其余图片按 Jellyfin 约定移入 `extrafanart/fanart1.jpg`、`extrafanart/fanart2.jpg` …

```bash
# 单个文件夹（自动检测是电影还是剧集）
uv run --project "$SKILL_DIR/scripts" "$SKILL_DIR/scripts/jellyfin_tool.py" rename "/path/to/Movie.Name.2020.1080P" --dry-run

# 批量处理整个目录
uv run --project "$SKILL_DIR/scripts" "$SKILL_DIR/scripts/jellyfin_tool.py" rename /path/to/Movies/ --batch --dry-run

# 手动指定标题（当自动解析不准时）
uv run --project "$SKILL_DIR/scripts" "$SKILL_DIR/scripts/jellyfin_tool.py" rename "/path/to/folder" --title "The Matrix" --year 1999

# 手动指定 IMDB ID（当 API 返回候选列表时使用）
uv run --project "$SKILL_DIR/scripts" "$SKILL_DIR/scripts/jellyfin_tool.py" rename "/path/to/folder" --imdb-id tt0133093 --yes

# 强制指定为剧集类型
uv run --project "$SKILL_DIR/scripts" "$SKILL_DIR/scripts/jellyfin_tool.py" rename "/path/to/folder" --type series
```

## 典型工作流

1. **先预览**：总是先用 `--dry-run` 查看解析结果是否正确
2. **处理候选列表**：若 API 返回多个候选，从列表选择正确的 IMDB ID 后用 `--imdb-id` 重试
3. **批量处理**：使用 `--batch` 时，失败的文件夹会在末尾汇总，逐一用 `--imdb-id` 补处理
4. **确认执行**：预览无误后去掉 `--dry-run` 加 `--yes` 执行

## 支持的文件格式

- 视频：`.mp4` `.mkv` `.avi` `.wmv` `.mov` `.ts` `.m2ts` `.flv` `.rmvb`
- 图片（poster/fanart）：`.jpg` `.jpeg` `.png` `.webp` `.gif` `.tbn`
- 字幕：`.srt` `.ass` `.ssa` `.sub` `.vtt`（保留原文件名不处理）
