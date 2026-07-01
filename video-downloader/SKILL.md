---
name: video-downloader
description: |
  下载视频工具。处理抖音短链、抖音作品页，以及 yt-dlp 支持的网站视频下载。

  触发场景：
  - 用户发来视频链接并要求”下载这个视频”
  - 用户要求下载抖音视频、图文、封面、音乐、JSON、评论
  - 用户要求下载 YouTube、Bilibili、X、TikTok 等 yt-dlp 支持站点的视频
  - 用户提到”yt-dlp””douyin-downloader””视频下载””抖音短链”
  - 用户要求获取抖音热搜榜或搜索抖音作品
  - 用户要求刷新抖音 Cookie
---

# Video Downloader Skill

统一封装两类下载能力：

- `douyin-downloader`：优先用于抖音，支持封面、音乐、JSON、评论等附加资源。
- `yt-dlp`：用于 yt-dlp 支持的网站视频下载。

## 配置来源

配置文件固定按以下顺序查找：

1. 项目根目录：`~/storage/ai_agent/skills/agent_config.toml`
2. 全局目录：`~/.agents/agent_config.toml`

不要使用其他目录猜测策略。

本 skill 的配置节为 `[video-downloader]`。模板文件位于 skill 目录：

`agent_config.example.toml`

职责边界：

- `agent_config.toml` 只保存本 skill 的高层配置，例如 backend、工具路径、默认输出目录。
- `douyin-downloader` 的原生运行配置继续保存在它自己的 `config.yml`。
- 不要把抖音 Cookie 或 `douyin-downloader` 的完整配置结构塞进 `agent_config.toml`。

## 脚本路径

相对于 skill 目录：

`scripts/video_downloader.py`

优先用这个脚本，不要手工拼装下载命令。

## 首先做什么

用户要求下载时，先检查环境：

```bash
python3 scripts/video_downloader.py doctor --json
```

判断规则：

- 抖音 URL：优先使用 `douyin` backend。
- 非抖音 URL：优先使用 `yt-dlp` backend。

如果目标 backend 不可用：

- 不要静默安装。
- 先问用户是要你安装，还是让用户在 `agent_config.toml` 里指定调用路径。

只有在用户明确同意后，才执行安装命令。

## 下载命令

### 抖音视频

仅视频：

```bash
python3 scripts/video_downloader.py download "https://v.douyin.com/xxxx/" --backend douyin --video-only
```

视频 + 音乐 + 封面 + JSON：

```bash
python3 scripts/video_downloader.py download "https://v.douyin.com/xxxx/" --backend douyin --with-assets
```

附带评论：

```bash
python3 scripts/video_downloader.py download "https://v.douyin.com/xxxx/" --backend douyin --with-assets --comments
```

### yt-dlp 支持站点

```bash
python3 scripts/video_downloader.py download "https://example.com/video" --backend yt-dlp
```

### 自动选择 backend

```bash
python3 scripts/video_downloader.py download "URL" --backend auto
```

## 安装命令

只有在用户明确同意后再运行。

安装或更新 `douyin-downloader`：

```bash
python3 scripts/video_downloader.py install-douyin
```

安装或更新 `yt-dlp`：

```bash
python3 scripts/video_downloader.py install-yt-dlp
```

## 刷新 Cookie

当抖音下载提示 Cookie 缺失或无效时，使用此命令启动浏览器登录：

```bash
python3 scripts/video_downloader.py refresh-cookies
```

这会打开浏览器窗口，用户完成抖音登录后，Cookie 会自动保存。

## 热搜榜

获取抖音热搜榜并保存为 JSONL 文件：

```bash
# 获取前 30 条热搜
python3 scripts/video_downloader.py hot-board --limit 30

# 获取全部热搜
python3 scripts/video_downloader.py hot-board --limit 0

# 指定输出目录
python3 scripts/video_downloader.py hot-board --limit 30 --output-dir ~/Downloads/hot-board
```

输出文件保存在 `hot_board/` 子目录，文件名格式为 `{timestamp}.jsonl`。

## 搜索作品

搜索抖音作品并保存为 JSONL 文件：

```bash
# 搜索关键词，默认最多 50 条
python3 scripts/video_downloader.py search "美食"

# 指定最大条数
python3 scripts/video_downloader.py search "美食" --max 100

# 指定输出目录
python3 scripts/video_downloader.py search "美食" --max 50 --output-dir ~/Downloads/search
```

输出文件保存在 `search/` 子目录，文件名格式为 `{keyword}_{timestamp}.jsonl`。

## 配置字段

`[video-downloader]` 支持这些字段：

```toml
[video-downloader]
default_backend = "auto"
default_output_dir = "~/Downloads/video-downloads"
runtime_dir = ""

yt_dlp_path = ""
yt_dlp_output_template = "%(title)s [%(id)s].%(ext)s"

douyin_downloader_home = ""
douyin_config_path = ""
```

说明：

- `yt_dlp_path`：显式指定 `yt-dlp` 可执行文件路径。
- `douyin_downloader_home`：显式指定 `douyin-downloader` 项目目录，目录内应包含 `run.py` 与 `pyproject.toml`。
- `douyin_config_path`：显式指定 `douyin-downloader` 使用的原生 `config.yml` 路径。
- `runtime_dir`：未显式指定工具路径时，本 skill 的默认安装目录。默认是 `video-downloader/.runtime/`。

## douyin 配置处理

调用抖音 backend 时，脚本行为是：

1. 读取 `douyin_config_path` 指向的 `config.yml`。
2. 若未配置该路径，则读取 `douyin_downloader_home/config.yml`。
3. 若默认 `config.yml` 不存在，则生成一份安全的默认配置。
4. 运行前生成一份临时请求配置，只覆盖本次下载需要的字段：
   `path`、`music`、`cover`、`avatar`、`json`、`comments`、`mode`

因此：

- 长期 Cookie、浏览器兜底、线程数等底层设置仍放在 `douyin-downloader` 自己的 `config.yml`。
- skill 只在单次请求层面覆写下载行为。

## Cookie 处理

抖音下载若提示 Cookie 缺失或无效，不要自行假设登录状态正常。

使用 `douyin-downloader` 时：

- 如果用户已配置自己的 `douyin_downloader_home`，则要求用户确认该路径下的 Cookie 是否可用。
- 如果是本 skill 安装的运行时目录，则说明需要用户登录一次抖音并刷新 Cookie。

本脚本不负责交互式登录。需要登录时，明确告诉用户下一步需要处理 Cookie。

## 输出要求

回复用户时保持简洁：

- 明确说下载是否成功。
- 给出输出目录或关键文件路径。
- 抖音缺少头像之类的附加资源时，只在相关时提一句。
- 若因缺少安装或配置而无法继续，明确指出缺的 backend，并给出“安装”或“配置路径”两个选项。
