---
name: object-storage
description: |
  独立的 S3 兼容对象存储与 CDN 管理工具。用于把本地文件上传到 AWS S3、腾讯云 COS、
  阿里云 OSS、火山 TOS、MinIO 等 S3 API 兼容目标；支持多 target、对象 key 前缀、覆盖保护、
  SHA-256 内容去重、上传后尺寸校验、公开 URL，以及 CloudFront、腾讯云 CDN、火山引擎 CDN 的刷新、预热与任务查询。
  当用户要求上传本地文件或递归上传目录到 bucket、比较远端对象是否变化、返回对象 URL、刷新 CDN 或预热资源时使用。
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

   上传入口文件或配置文件时显式设置缓存策略：

   ```bash
   object-storage upload /local/index.html --target archive --key index.html \
     --cache-control 'no-cache,max-age=0,must-revalidate' --overwrite --json
   ```

   覆盖已有对象但未传 `--cache-control` 时，保留该对象原有的 `Cache-Control`；
   新对象未传时不自动添加。真实结果中的 `cache_control` 是上传后 HEAD 回读值。

5. 递归上传目录使用独立的 `upload-tree`。它默认递归，不需要也不接受 `-r`；
   `--key-prefix` 是目标目录，源目录本身的名称不会进入 object key：

   ```bash
   object-storage upload-tree /local/dist --target archive \
     --key-prefix releases/v1 --dry-run --json
   object-storage upload-tree /local/dist --target archive \
     --key-prefix releases/v1 --workers 4 \
     --cache-control 'no-cache,max-age=0,must-revalidate' --json
   ```

   例如 `/local/dist/assets/logo.png` 映射为
   `<target prefix>/releases/v1/assets/logo.png`。符号链接不会被上传。

## 命令

| 命令 | 作用 |
|---|---|
| `doctor` / `list` | 校验并列出配置，不连接远端 |
| `resolve-key KEY` | 显示 target prefix 处理后的最终 key |
| `upload LOCAL_PATH` | 上传一个本地文件 |
| `upload-tree DIRECTORY` | 默认递归上传目录内容并保留相对路径 |
| `cdn purge-url` | 按完整 URL 或 object key 刷新文件缓存 |
| `cdn purge-path` | 按完整目录 URL 或 object key 刷新目录缓存 |
| `cdn prefetch` | 腾讯云或火山引擎 CDN 资源预热 |
| `cdn status` | 查询 CloudFront 或火山引擎 CDN 任务状态（`--task-id ID`） |

CDN 默认优先目录刷新（传目录，不手写 `*`）；先 dry-run，再执行：

```bash
object-storage cdn purge-path --target archive --keys project/ --flush-type flush --dry-run --json
object-storage cdn purge-path --target archive --keys project/ --flush-type flush --json
object-storage cdn prefetch --target archive --keys project/video.mp4 --area mainland --json
```

CloudFront 配置 `provider = "cloudfront"` 与 `distribution_id`，复用 S3 target 凭据。
具体配置及权限见 [配置参考](references/configuration.md#aws-cloudfront)。
CloudFront 不支持预热；目录刷新转为 `/dir/*`。

火山引擎 CDN 配置 `provider = "volcengine"`，可复用 TOS target 的显式 AK/SK；
目录刷新提交以 `/` 结尾的目录 URL。具体权限和限制见
[配置参考](references/configuration.md#火山引擎-cdn)。

## CDN 刷新策略（AWS、腾讯云与火山引擎通用）

- 默认优先目录/通配符刷新，尽量避免逐文件 `purge-url`；同一批变更先汇总，
  按本次发布或资源目录合并、去重，去掉已被父目录覆盖的子目录，再统一提交。
- 选择覆盖本次变更的最小合理业务目录。例如 `project/video/a.mp4` 和
  `project/video/b.mp4` 合并为 `project/video/`。不要为了压成一条而把不同项目
  扩大为全站；跨项目分别刷新。仅在用户要求精确文件刷新、目录扩大范围不合适，
  或 provider 不支持目录刷新时使用单文件路径。
- 统一使用 `cdn purge-path --keys project/video/`：CloudFront 自动生成
  `/project/video/*`；腾讯云提交目录 URL 到 `PurgePathCache`；火山引擎提交 `type=dir`
  的目录 URL。不要把 `*` 传给腾讯云或火山引擎。
  腾讯云默认 `--flush-type flush`（刷新变更资源），需要目录内全部缓存失效时用 `delete`。
- 手动 CDN `--keys` 必须包含真实对象 key 的 target prefix。执行前用 `--dry-run`
  检查最终范围，说明合并后的目录数/失效路径数；不要自动扩大到 `/*`。
- `upload-tree` 在 `purge_on_upload = true` 时不会逐文件刷新：上传完成后按目标
  `prefix`/`--key-prefix` 合并为目录刷新。两者都为空时按第一级目录合并，目标根目录下
  的文件才使用精确文件刷新，避免自动扩大为全站刷新。全部 `skipped_unchanged` 时不刷新。
- AWS 每月前 1000 个失效路径免费，同一 AWS 账户下所有分配合并计算；
  一个通配符路径无论覆盖多少文件都计一个路径。将多个路径放进一次 API 请求
  仍按路径数量计费，不能将请求次数当作额度用量。未查询账户当月总用量时，
  不声称本次一定免费或给出剩余额度。
- 上述合并策略适用于腾讯云与火山引擎；AWS 的 1000 路径免费额度不适用于它们。
  计费依据和各 provider 的目录额度见
  [CDN 刷新额度与合并示例](references/configuration.md#cdn-刷新额度与合并示例)。

## 关键语义

- `--key` 是 target `prefix` 之后的相对 key；禁止绝对路径和 `..`。
- 未传 `--key` 时使用本地文件名，不自动保留本地完整目录。
- `upload-tree` 的 `--key-prefix` 也位于 target `prefix` 之后；默认递归、保留源目录内的
  相对路径，不包含源目录名称。上传前会完成所有覆盖冲突预检，避免发现冲突前已写入部分文件。
- `upload-tree --workers` 控制同时处理的文件数；每个大文件内部仍使用 target 的
  `max_concurrency` multipart 配置，按存储服务连接限制合理设置，避免并发乘积过大。
- `upload` 与 `upload-tree` 都支持 `--cache-control VALUE`。目录命令会把同一值应用于
  本次实际上传的所有文件；混合 HTML 与带内容哈希资源时不要强行使用一种策略，可拆分上传。
  省略参数覆盖对象时保留旧值，显式传参时替换旧值，新对象省略时保持未设置。
- 每次真实上传都写入对象 metadata `content-sha256`，并在上传后用 `head_object`
  校验字节数。
- `--if-changed` 只比较远端 `ContentLength` 和 `content-sha256`；不把 ETag 当作通用摘要。
- 显式 `--cache-control` 与远端值不同时，即使文件内容未变化也会重新上传以更新对象头。
- 旧对象没有摘要时会重新上传一次并补齐 metadata。
- 内容相同而跳过上传时，不执行 `purge_on_upload` CDN 刷新。
- `public_base_url` 用于返回公开对象 URL；`cdn.base_url` 用于 CDN 操作，两者不可混用。
- COS 兼容参数会关闭非必要的 aws-chunked checksum，multipart 使用普通
  `Content-Length`。
- 不输出 access key、secret key、session token 或包含凭据的异常上下文。

## 结果

上传成功必须报告 target、bucket、object key、字节数、SHA-256、`cache_control` 和可用的公开 URL。
目录上传还要报告总数、上传/跳过/失败数量、失败清单和合并后的 CDN 任务。
CDN 命令报告 operation、status、TaskId 与目标 URL；提交不代表缓存已经生效，不做虚假验证。
