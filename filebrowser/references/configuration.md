# 配置参考

## 职责边界

`[filebrowser]` 只保存 FileBrowser source 与临时目录。S3 兼容 target、凭据、bucket、prefix
和 CDN 配置属于独立 `[object-storage]` section。两个 section 可以位于同一个
`agent_config.toml`，但由不同 Skill 分别解析。

```toml
[filebrowser]
default_source = "production"
staging_dir = ""

[filebrowser.sources.production]
adapter = "filebrowser"
base_url = "https://files.example.com"
token = ""
token_env = "FILEBROWSER_TOKEN"
source = "项目"
verify_tls = true
timeout_seconds = 600
max_transfer_bytes = 0
upload_chunk_bytes = 16777216

[object-storage]
default_target = "archive"

[object-storage.targets.archive]
adapter = "s3"
bucket = "archive-bucket"
prefix = "filebrowser"
access_key_id_env = "ARCHIVE_S3_ACCESS_KEY_ID"
secret_access_key_env = "ARCHIVE_S3_SECRET_ACCESS_KEY"
```

完整的 S3/COS/CDN 参数、覆盖与 SHA-256 语义见相邻
`object-storage/references/configuration.md`。

## FileBrowser source

- `base_url`：FileBrowser Quantum HTTP(S) 地址。
- `token` / `token_env`：直接 token 或保存 token 的环境变量名，推荐后者。
- `source`：FileBrowser Quantum 内部 source 名称。
- `timeout_seconds`：HTTP 请求超时，至少 1 秒。
- `max_transfer_bytes`：单文件限制，`0` 表示不设置 Skill 级限制。
- `upload_chunk_bytes`：`put` 上传时每个流式块的字节数。
- `staging_dir`：FileBrowser 转存至 object-storage 时的临时目录；完成或失败后清理。

## 委托协议

`filebrowser upload` 先把单个远端文件流式下载到临时文件，再通过 object-storage CLI/JSON
接口上传。默认 key 是 FileBrowser 绝对路径去掉开头 `/`；`--key` 可以覆盖。object-storage
负责添加 target prefix、覆盖检查、SHA-256 比较、上传后尺寸校验和自动 CDN 刷新。

object-storage 查找顺序：

1. `OBJECT_STORAGE_SKILL_DIR` 指定的 Skill 根目录或 `scripts` 目录。
2. 当前目录祖先中的 `skills/object-storage`、`.agents/skills/object-storage` 或
   `.claude/skills/object-storage`。
3. `~/.agents/skills/object-storage`、`~/.claude/skills/object-storage`。
4. 当前 Skills 仓库内与 `filebrowser` 相邻的 `object-storage`。

纯 FileBrowser 文件管理、`get` 和 `put` 不会启动或要求 object-storage。
