---
name: filebrowser
description: |
  FileBrowser Quantum 一体化 CLI。包含两类操作：
    1) 文件管理：浏览（info/list-dir）、读写（update）、创建目录（mkdir）、删除（delete）、
       移动/重命名/复制（move）、搜索（search）、预览（preview）、多文件打包下载（download-files）。
    2) 传输分发：FileBrowser ↔ 本地（get/put）、FileBrowser → S3 兼容 bucket（upload，AWS S3 /
       腾讯云 COS / 阿里云 OSS / 火山 TOS）、对象 key 映射、分块上传、覆盖保护、内容去重和 dry-run。
       支持腾讯云 CDN 缓存管理：刷新 URL/目录、预热，上传后可自动刷新。

  支持多 FileBrowser source。所有 HTTP 访问都走共享 FileBrowserClient；下游 skill 不应自行
  实现 FileBrowser 请求。`filebrowser-quantum` skill 已合并到本 skill，请使用 `filebrowser`。

  当用户提到 FileBrowser 上传下载、文件管理、远程目录创建、S3 转存、CDN 刷新预热、漫剧交付
  素材同步或远端文件操作时使用。
---

# FileBrowser Skill

## 执行入口

```bash
uv run --project {SKILL_DIR}/scripts filebrowser --non-interactive <command>
```

配置查找顺序为：当前工作目录、skill 目录、当前 Git 项目根目录、`~/.agents/agent_config.toml`。也可以用全局 `--config PATH` 显式指定；显式路径不存在时直接停止。

完整配置结构和多 S3 示例见 [references/configuration.md](references/configuration.md)。不要输出 FileBrowser token、S3 secret 或包含它们的异常详情。

## 子命令总览

按职责分两组。所有命令都支持 `--source <name>` 切换 FileBrowser source，省略时用 `[filebrowser] default_source`。

### 文件管理（FileBrowser 自身操作）

| 命令 | 作用 |
|---|---|
| `info` | 读资源元数据；`--content` 内联小文本内容 |
| `list-dir` | 列目录直接子项 |
| `mkdir` | 创建目录（已存在返回原路径，不覆盖） |
| `update` | 从 stdin 覆盖写入文件；`--override` 才允许覆盖 |
| `delete` | 删除文件或目录 |
| `move` | 重命名 / 移动 / 复制；`--action rename\|copy`，`--overwrite` 才允许覆盖 |
| `search` | 文件名搜索；`--scope` 限定目录 |
| `preview` | 下载缩略图；`--size` 控制尺寸，`--output` 指定输出 |
| `download-files` | 多文件打包下载；`--files` 用 `||` 分隔（如 `proj::/a.txt||proj::/b.txt`），`--algo zip\|tar\|tar.gz` |
| `sources` | 列出 source 信息 |

### 传输分发（跨存储 / CDN）

| 命令 | 作用 |
|---|---|
| `doctor` | 验证配置不联网 |
| `list` | 列出 sources 与 targets |
| `get` | FileBrowser 文件 → 本地 |
| `put` | 本地文件 → FileBrowser（单文件，**默认拒绝覆盖**） |
| `upload` | FileBrowser 文件 → S3 target（**注意方向，与 `put` 相反**） |
| `cdn purge-url` / `cdn purge-path` / `cdn prefetch` | 腾讯云 CDN 缓存管理 |

## 文件管理示例

```bash
# 1) 读 SRT 内容（漫剧翻译场景）
filebrowser info --source 项目 --path /虎澈漫剧/C01字幕/英文/孤岛05.srt --content --json

# 2) 创建多语种字幕目录（已存在则直接返回原路径，不会重复创建）
filebrowser mkdir --source 项目 --path /虎澈漫剧/C01字幕/巴西葡萄牙语SRT/ --json

# 3) 写回翻译后的 SRT（注意 --override）
cat outputs/tmp/.../巴西葡萄牙语SRT/孤岛05.srt \
  | filebrowser update --source 项目 --path /虎澈漫剧/C01字幕/巴西葡萄牙语SRT/孤岛05.srt --override --json

# 4) 搜索远端文件
filebrowser search --source 项目 --query 孤岛 --scope /虎澈漫剧 --json

# 5) 打包下载一个项目的成片 + 字幕
filebrowser download-files --source 项目 \
  --files "项目::/虎澈漫剧/B06.../成片/EP01.mp4||项目::/虎澈漫剧/B06.../字幕/EP01.srt" \
  --algo zip --output /tmp/B06.zip --json

# 6) 重命名 / 复制（--action rename|copy）
filebrowser move --source 项目 --from /虎澈漫剧/B06/old.mp4 --destination /虎澈漫剧/B06/new.mp4 \
  --action rename --json
```

## 传输分发工作流

1. 首次操作或配置变化后运行：

   ```bash
   filebrowser --config /path/agent_config.toml doctor --json
   ```

2. 上传前先 dry-run，确认 source、target 和 object key：

   ```bash
   filebrowser upload "/项目/成片/demo.mp4" \
     --source production --target archive --dry-run --json
   ```

3. 用户确认目标后执行上传：

   ```bash
   filebrowser upload "/项目/成片/demo.mp4" \
     --source production --target archive --json
   ```

4. 要覆盖但只在内容变化时写入，使用 `--overwrite --if-changed`。相同文件会跳过上传，
   `--json` 输出中的 `unchanged_files` 会列出每个相同文件的源路径、对象 key、大小和 SHA-256：

   ```bash
   filebrowser upload "/项目/成片/demo.mp4" \
     --source production --target archive --overwrite --if-changed --json
   ```

5. 默认保留 FileBrowser 完整相对路径作为 S3 key，并在前面添加 target 的 `prefix`。需要改 key 时使用 `--key`。

### 上传本地文件回 FileBrowser

`put` 不依赖 S3 target，适合把 `media-use` 生成的成片传回主视频同目录。上传前先
dry-run 确认本地文件、source 和目标远端路径：

```bash
filebrowser put /local/output_480p_branded.mp4 \
  "/项目/成片/output_480p_branded.mp4" --source production --dry-run --json
```

确认后执行：

```bash
filebrowser put /local/output_480p_branded.mp4 \
  "/项目/成片/output_480p_branded.mp4" --source production --json
```

大文件按 `upload_chunk_bytes` 流式分块发送，上传后读取远端元数据并校验字节数。默认
拒绝覆盖，只有用户明确要求替换既有文件时才添加 `--overwrite`。

### 下载 FileBrowser 文件到本地

`get` 将单个远端文件流式下载至一个不存在的本地路径，完成后校验下载字节数。适合在
品牌视频处理前取回主视频、Logo 与片尾：

```bash
filebrowser get "/项目/成片/demo.mp4" /local/demo.mp4 \
  --source production --json
```

### 品牌视频回传流程

主视频、Logo、片尾先下载到本地临时目录；使用 `media-use` 的 `ffmpeg_brand` 输出成片，
再以 `put` 上传至主视频所在目录。`filebrowser` 只处理下载和回传，水印、片尾、分辨率
和帧率参数由 `media-use` 处理，避免两个 skill 的职责混杂。

## 安全规则

- 一次只上传一个文件；目录会被拒绝。
- S3 已存在同 key 对象时默认拒绝。只有用户明确要求覆盖时才添加 `--overwrite`。
- `--if-changed` 必须与 `--overwrite` 同时使用。上传对象带 SHA-256 元数据；启用后只有大小和
  SHA-256 都一致才跳过，并在结果的 `unchanged_files` 明确列出相同文件。旧对象缺少该元数据时会
  上传一次以补齐，不以 ETag 判断内容。跳过上传时不会执行 CDN 刷新。
- `put` 的远端路径必须是绝对路径且不能包含 `..`；若目标已存在，默认拒绝覆盖。
- `get` 的本地目标路径必须不存在，避免无意覆盖已有文件。
- FileBrowser 路径必须是绝对路径且不能包含 `..`；S3 key 必须是相对路径且不能包含 `..`。
- `mkdir` 不会覆盖已存在的目录，已存在则返回原路径。
- `update` 默认 `override=false`，需要覆盖时显式加 `--override`。
- `move` 默认 `overwrite=false`，需要覆盖时显式加 `--overwrite`。
- 下载采用流式写入临时文件，不把大文件完整载入内存；优先使用下载响应的
  `Content-Length` 校验字节数，缺失时才回退至资源元数据；无论成功或失败都会清理临时目录。
- `max_transfer_bytes = 0` 表示不设 skill 级大小上限；生产配置建议设置明确上限。
- dry-run 只验证配置和路径，不连接 FileBrowser 或 S3，也不解析凭据链。

## CDN 缓存管理

为 target 配置可选的 `[filebrowser.targets.<name>.cdn]` 子表（`provider = "tencent"`）后，
可管理腾讯云 CDN 缓存，三个独立功能：

```bash
filebrowser cdn purge-url --target archive --keys "path/file.mp4"
filebrowser cdn purge-path --target archive --paths "https://cdn.example.com/path/" --flush-type flush
filebrowser cdn prefetch --target archive --urls "https://cdn.example.com/path/file.mp4" --area mainland
```

设 `purge_on_upload = true` 时 `upload` 成功后自动刷新该文件 URL（失败不影响上传）。
凭据复用 target 的 AK/SK，但需在腾讯云 CAM 授予 `cdn:PurgeUrlsCache`/`PurgePathCache`/
`PushUrlsCache` 权限。`cdn.base_url` 是 CDN 域名，与源站域名 `public_base_url` 不同。详见
[references/configuration.md](references/configuration.md)。

## 结果

转存 S3 成功时报告 source、FileBrowser 路径、target、bucket、object key、字节数、SHA-256 和可用的公开 URL。内容相同而跳过时，`skipped_unchanged` 为 `true`，且 `unchanged_files` 必须逐项列出相同文件；不要把该情况表述为已重新上传。`put` 成功时报告 source、本地路径、FileBrowser 路径和字节数。不要声称上传成功，除非 S3 `head_object` 或 FileBrowser 下载端点的 `Content-Length` 与本地文件一致；缺失该响应头时才回退至资源元数据。
