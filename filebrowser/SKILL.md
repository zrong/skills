---
name: filebrowser
description: 从 FileBrowser Quantum 下载远程文件、上传本地文件回 FileBrowser，或转存到 S3 兼容 bucket。支持多个 FileBrowser source、多个 S3 target、AWS S3/腾讯云 COS/阿里云 OSS/火山 TOS、对象 key 映射、分块上传、覆盖保护和 dry-run。支持腾讯云 CDN 缓存管理：刷新 URL/目录、预热，上传后可自动刷新。当用户提到 FileBrowser 上传下载、回传视频、文件转存 S3、对象存储备份、跨存储传输、CDN 刷新或预热时使用。
---

# FileBrowser Transfer

将一个 FileBrowser 文件暂存到本地临时目录，再上传到已配置的 target；也可将本地文件直接上传回 FileBrowser。当前外部 target adapter 为 `s3`；新增外部目标类型时扩展 adapter，不要把目标逻辑写入 FileBrowser 客户端。

## 执行入口

```bash
uv run --project {SKILL_DIR}/scripts fb-transfer --non-interactive <command>
```

配置查找顺序为：当前工作目录、skill 目录、当前 Git 项目根目录、`~/.agents/agent_config.toml`。也可以用全局 `--config PATH` 显式指定；显式路径不存在时直接停止。

完整配置结构和多 S3 示例见 [references/configuration.md](references/configuration.md)。不要输出 FileBrowser token、S3 secret 或包含它们的异常详情。

## 工作流

1. 首次操作或配置变化后运行：

   ```bash
   uv run --project {SKILL_DIR}/scripts fb-transfer --config /path/agent_config.toml doctor --json
   ```

2. 上传前先 dry-run，确认 source、target 和 object key：

   ```bash
   uv run --project {SKILL_DIR}/scripts fb-transfer --config /path/agent_config.toml \
     --non-interactive upload "/项目/成片/demo.mp4" \
     --source production --target archive --dry-run --json
   ```

3. 用户确认目标后执行上传：

   ```bash
   uv run --project {SKILL_DIR}/scripts fb-transfer --config /path/agent_config.toml \
     --non-interactive upload "/项目/成片/demo.mp4" \
     --source production --target archive --json
   ```

4. 默认保留 FileBrowser 完整相对路径作为 S3 key，并在前面添加 target 的 `prefix`。需要改 key 时使用 `--key`。

### 上传本地文件回 FileBrowser

`put` 不依赖 S3 target，适合把 `media-use` 生成的成片传回主视频同目录。上传前先
dry-run 确认本地文件、source 和目标远端路径：

```bash
uv run --project {SKILL_DIR}/scripts fb-transfer --config /path/agent_config.toml \
  --non-interactive put /local/output_480p_branded.mp4 \
  "/项目/成片/output_480p_branded.mp4" --source production --dry-run --json
```

确认后执行：

```bash
uv run --project {SKILL_DIR}/scripts fb-transfer --config /path/agent_config.toml \
  --non-interactive put /local/output_480p_branded.mp4 \
  "/项目/成片/output_480p_branded.mp4" --source production --json
```

大文件按 `upload_chunk_bytes` 流式分块发送，上传后读取远端元数据并校验字节数。默认
拒绝覆盖，只有用户明确要求替换既有文件时才添加 `--overwrite`。

### 下载 FileBrowser 文件到本地

`get` 将单个远端文件流式下载至一个不存在的本地路径，完成后校验下载字节数。适合在
品牌视频处理前取回主视频、Logo 与片尾：

```bash
uv run --project {SKILL_DIR}/scripts fb-transfer --config /path/agent_config.toml \
  --non-interactive get "/项目/成片/demo.mp4" /local/demo.mp4 \
  --source production --json
```

### 品牌视频回传流程

主视频、Logo、片尾先下载到本地临时目录；使用 `media-use` 的 `ffmpeg_brand` 输出成片，
再以 `put` 上传至主视频所在目录。`filebrowser` 只处理下载和回传，水印、片尾、分辨率
和帧率参数由 `media-use` 处理，避免两个 skill 的职责混杂。

## 安全规则

- 一次只上传一个文件；目录会被拒绝。
- S3 已存在同 key 对象时默认拒绝。只有用户明确要求覆盖时才添加 `--overwrite`。
- `put` 的远端路径必须是绝对路径且不能包含 `..`；若目标已存在，默认拒绝覆盖。
- `get` 的本地目标路径必须不存在，避免无意覆盖已有文件。
- FileBrowser 路径必须是绝对路径且不能包含 `..`；S3 key 必须是相对路径且不能包含 `..`。
- 下载采用流式写入临时文件，不把大文件完整载入内存；优先使用下载响应的
  `Content-Length` 校验字节数，缺失时才回退至资源元数据；无论成功或失败都会清理临时目录。
- `max_transfer_bytes = 0` 表示不设 skill 级大小上限；生产配置建议设置明确上限。
- dry-run 只验证配置和路径，不连接 FileBrowser 或 S3，也不解析凭据链。

## CDN 缓存管理

为 target 配置可选的 `[filebrowser.targets.<name>.cdn]` 子表（`provider = "tencent"`）后，
可管理腾讯云 CDN 缓存，三个独立功能：

```bash
uv run --project {SKILL_DIR}/scripts fb-transfer --non-interactive cdn purge-url \
  --target archive --keys "path/file.mp4"
uv run --project {SKILL_DIR}/scripts fb-transfer --non-interactive cdn purge-path \
  --target archive --paths "https://cdn.example.com/path/" --flush-type flush
uv run --project {SKILL_DIR}/scripts fb-transfer --non-interactive cdn prefetch \
  --target archive --urls "https://cdn.example.com/path/file.mp4" --area mainland
```

设 `purge_on_upload = true` 时 `upload` 成功后自动刷新该文件 URL（失败不影响上传）。
凭据复用 target 的 AK/SK，但需在腾讯云 CAM 授予 `cdn:PurgeUrlsCache`/`PurgePathCache`/
`PushUrlsCache` 权限。`cdn.base_url` 是 CDN 域名，与源站域名 `public_base_url` 不同。详见
[references/configuration.md](references/configuration.md)。

## 结果

转存 S3 成功时报告 source、FileBrowser 路径、target、bucket、object key、字节数和可用的公开 URL。`put` 成功时报告 source、本地路径、FileBrowser 路径和字节数。不要声称上传成功，除非 S3 `head_object` 或 FileBrowser 下载端点的 `Content-Length` 与本地文件一致；缺失该响应头时才回退至资源元数据。
