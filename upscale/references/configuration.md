# 配置

## 查找顺序

`upscale` 读取共享 `agent_config.toml`，顺序为：

1. `--config PATH`；
2. `UPSCALE_CONFIG`；
3. 当前工作目录；
4. skill 根目录；
5. 当前工作目录向上的最近 Git 根目录；
6. `~/.agents/agent_config.toml`。

显式路径或 `UPSCALE_CONFIG` 指向的文件不存在时立即失败，不继续回退。`filebrowser` 默认按自身标准顺序发现配置；只有设置 `filebrowser_config` 时才传递显式配置路径。

## `[upscale]`

| 字段 | 必需 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `base_url` | 是 | - | upscale-api 根 URL，不含末尾 `/` |
| `timeout` | 否 | `3600` | 单次上传、查询或下载的 HTTP 超时秒数 |
| `poll_interval` | 否 | `2` | 任务轮询间隔秒数，范围 `0.1..60` |
| `max_wait_seconds` | 否 | `86400` | 异步任务最长等待时间 |
| `max_input_bytes` | 否 | `21474836480` | 本地或已下载输入的大小上限；`0` 表示不设 skill 级限制 |
| `api_key` / `api_key_env` | 否 | 空 | 可选网关认证；不要提交真实 key |
| `auth_header` | 否 | `Authorization` | key 所在 header |
| `auth_scheme` | 否 | `Bearer` | header 值前缀；空字符串表示直接发送 key |
| `filebrowser_skill_dir` | 否 | 自动查找 | 独立 filebrowser skill 根目录或 `scripts` 目录 |
| `filebrowser_config` | 否 | 独立发现 | FileBrowser 专用 `agent_config.toml` 路径 |

`[upscale.headers]` 可配置额外 header，值支持 `${ENV_NAME}`。诊断输出只报告配置文件路径，不回显 header 值。

## FileBrowser 依赖查找

依次检查 `[upscale].filebrowser_skill_dir`、`FILEBROWSER_SKILL_DIR`、当前 skill 的相邻 `filebrowser`、当前 Git 项目的 `.agents/skills/filebrowser`，以及 `~/.agents/skills/filebrowser`。找到后始终通过其 `uv` CLI 调用 `get` 和 `put`。
