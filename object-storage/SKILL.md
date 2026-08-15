---
name: object-storage
description: |
  独立的 S3 兼容对象存储与 CDN 管理工具。用于把本地文件上传到 AWS S3、腾讯云 COS、
  阿里云 OSS、火山 TOS、MinIO 等 S3 API 兼容目标；支持多 target、对象 key 前缀、覆盖保护、
  SHA-256 内容去重、上传后尺寸校验、公开 URL，以及腾讯云 CDN URL/目录刷新和预热。
  当用户要求上传本地文件到 bucket、比较远端对象是否变化、返回对象 URL、刷新 CDN 或预热资源时使用。
---

# Object Storage Skill

## 执行入口

```bash
uv run --project {SKILL_DIR}/scripts object-storage --non-interactive <command>
```

配置查找顺序：当前工作目录、Skill 目录、当前 Git 项目根目录、
`~/.agents/agent_config.toml`。也可在子命令前传 `--config PATH`。完整配置见
[references/configuration.md](references/configuration.md)，模板见
[agent_config.example.toml](agent_config.example.toml)。

## 工作流

1. 首次使用或配置变化后检查配置，不联网：

   ```bash
   object-storage doctor --json
   object-storage list --json
   ```

2. 上传前用 dry-run 检查本地文件、target 和最终 object key：

   ```bash
   object-storage upload /local/video.mp4 \
     --target archive --key project/video.mp4 --dry-run --json
   ```

3. 默认上传会拒绝覆盖同 key 对象。确认目标不存在后执行：

   ```bash
   object-storage upload /local/video.mp4 \
     --target archive --key project/video.mp4 --json
   ```

4. 用户明确要求无条件覆盖时传 `--overwrite`。只在内容变化时覆盖，必须同时传
   `--overwrite --if-changed`：

   ```bash
   object-storage upload /local/video.mp4 --target archive \
     --key project/video.mp4 --overwrite --if-changed --json
   ```

   内容相同时 `skipped_unchanged=true`，并在 `unchanged_files` 明确列出本地路径、
   object key、大小和 SHA-256。向用户说明该列表，不要声称文件已重新上传。

## 命令

| 命令 | 作用 |
|---|---|
| `doctor` / `list` | 校验并列出配置，不连接远端 |
| `resolve-key KEY` | 显示 target prefix 处理后的最终 key |
| `upload LOCAL_PATH` | 上传一个本地文件 |
| `cdn purge-url` | 按完整 URL 或 object key 刷新文件缓存 |
| `cdn purge-path` | 按完整目录 URL 或 object key 刷新目录缓存 |
| `cdn prefetch` | 按完整 URL 或 object key预热资源 |

CDN 示例：

```bash
object-storage cdn purge-url --target archive --keys project/video.mp4 --json
object-storage cdn purge-path --target archive --keys project/ --flush-type flush --json
object-storage cdn prefetch --target archive --keys project/video.mp4 --area mainland --json
```

## 关键语义

- `--key` 是 target `prefix` 之后的相对 key；禁止绝对路径和 `..`。
- 未传 `--key` 时使用本地文件名，不自动保留本地完整目录。
- 每次真实上传都写入对象 metadata `content-sha256`，并在上传后用 `head_object`
  校验字节数。
- `--if-changed` 只比较远端 `ContentLength` 和 `content-sha256`；不把 ETag 当作通用摘要。
- 旧对象没有摘要时会重新上传一次并补齐 metadata。
- 内容相同而跳过上传时，不执行 `purge_on_upload` CDN 刷新。
- `public_base_url` 用于返回公开对象 URL；`cdn.base_url` 用于 CDN 操作，两者不可混用。
- COS 兼容参数会关闭非必要的 aws-chunked checksum，multipart 使用普通
  `Content-Length`。
- 不输出 access key、secret key、session token 或包含凭据的异常上下文。

## 结果

上传成功必须报告 target、bucket、object key、字节数、SHA-256 和可用的公开 URL。
CDN 命令报告 operation、status、TaskId 与目标 URL；提交不代表缓存已经生效，不做虚假验证。
