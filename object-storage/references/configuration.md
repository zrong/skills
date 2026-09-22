# 配置参考

## Section 所有权

`object-storage` 独立拥有 `[object-storage]`、`targets.<name>` 和可选的 `cdn` 子表。
FileBrowser 等调用方只传本地路径、target 与 object key，不读取或复制 S3/CDN 配置。

```toml
[object-storage]
default_target = "archive"

[object-storage.targets.archive]
adapter = "s3"
bucket = "archive-bucket"
region = "ap-guangzhou"
endpoint_url = "https://cos.ap-guangzhou.myqcloud.com"
public_base_url = "https://static.example.com"
prefix = "uploads"
access_key_id_env = "ARCHIVE_S3_ACCESS_KEY_ID"
secret_access_key_env = "ARCHIVE_S3_SECRET_ACCESS_KEY"
addressing_style = "virtual"
multipart_threshold_bytes = 8388608
multipart_chunksize_bytes = 8388608
max_concurrency = 4
verify_tls = true
```

增加命名 target 即可支持多个 bucket。`--target` 选择 target，未传时使用
`default_target`。

## 凭据

- `profile`、成对的显式 access/secret、boto3 默认凭据链三种方式互斥选择。
- `*_env` 的值是环境变量名称，不是凭据本身。
- 声明显式凭据后，环境变量缺失会直接失败，不回退默认链。
- `session_token` 只能与显式 access/secret 一起使用。
- 示例与仓库配置不得包含真实 secret。

## 寻址风格（addressing_style）

决定 bucket 名在请求 URL 中的位置，原样传给 boto3
`Config(s3={"addressing_style": ...})`，语义以 botocore 文档为准。

| 取值 | 请求形态 | 适用场景 |
|---|---|---|
| `virtual` | `https://{bucket}.{endpoint_host}/{key}` | AWS S3，或网关已配置泛域名解析与证书 |
| `path` | `https://{endpoint_host}/{bucket}/{key}` | 任何 S3 兼容存储；自建服务首选 |
| `auto` | 由 botocore 判断 | endpoint 为 IP 时等价 `path`；自定义域名时可能选 `virtual`，行为随 SDK 版本变化，不建议依赖 |

自建 S3 兼容存储（MinIO、RustFS、Garage 等）通常没有 `*.{endpoint}` 泛域名 DNS，
`virtual`（或 `auto` 落到 virtual）会把 bucket 拼进主机名导致解析失败。实测案例：
endpoint `https://s3.example.games` + bucket `public` 实际请求
`public.s3.example.games`，报 `nodename nor servname provided`。此类 target 应显式
配置 `addressing_style = "path"`。

## Key 与上传

- `prefix` 与 `--key` 都必须是相对 POSIX key，禁止 `..`。
- multipart 参数直接传给 boto3 `TransferConfig`。
- 上传后通过 `head_object` 校验 `ContentLength`，所有新对象写入
  `content-sha256` metadata。
- `--overwrite --if-changed` 只在大小和 metadata 摘要均一致时跳过；旧对象没有摘要时
  会重新上传一次。ETag 不用于跨厂商内容判断。
- `upload-tree DIRECTORY` 默认递归上传普通文件，跳过符号链接，并保留 DIRECTORY 内的
  相对路径。`--key-prefix` 位于 target `prefix` 之后，不包含源目录名称。
- 目录上传在开始写入前检查全部目标 key 的覆盖冲突；实际传输复用同一个 target 客户端，
  `--workers` 控制文件级并发。文件级并发会与 multipart `max_concurrency` 叠加。
- `purge_on_upload = true` 时，目录上传在批次结束后统一刷新 `prefix`/`--key-prefix`
  对应目录，不对每个文件单独提交刷新。目标根目录下的文件无法安全归并为目录时使用
  文件刷新；全部文件未变化时不刷新。

### Cache-Control

- `upload` 和 `upload-tree` 支持 `--cache-control VALUE`，该值作为对象的系统元数据写入。
- 覆盖已有对象但省略参数时，先读取并保留旧对象的 `Cache-Control`，避免入口文件原有的
  `no-cache,max-age=0,must-revalidate` 被覆盖清除；显式传参会替换旧值。
- 新对象省略参数时保持未设置，不假定所有文件使用相同缓存策略。
- `upload-tree --cache-control` 应用于目录内所有实际上传文件。站点通常应区分入口文件和
  带内容哈希的静态资源；需要不同策略时拆分上传，不要给整个混合目录强制同一个值。
- 上传结果中的 `cache_control` 来自上传后 `head_object` 回读，可用于验证实际落盘结果。
- 使用 `--overwrite --if-changed --cache-control VALUE` 时，只有内容摘要、大小和
  `Cache-Control` 都一致才跳过；仅缓存策略变化也会重新上传对象。

## 下载

- `download KEY --output PATH` 的 KEY 位于 target `prefix` 之后，默认拒绝覆盖本地文件；
  显式传 `--overwrite` 才替换。
- `download-tree PREFIX --output DIRECTORY` 递归下载 PREFIX 内的对象，输出目录不包含
  PREFIX 这一层；下载前会枚举对象并检查所有本地输出冲突。
- 下载先写同目录临时文件，回读大小并在对象具有 `content-sha256` metadata 时验证 SHA-256，
  成功后才原子替换输出文件。旧对象没有该 metadata 时仍完成大小校验，但结果标记
  `sha256_verified=false`。

## 对象元数据查询

- `head KEY --target TARGET` 直接调用对象存储的 `HeadObject`，不下载对象正文。
- 成功返回大小、Content-Type、Cache-Control、ETag、VersionId 和 `content-sha256` metadata。
- 只有明确的 `404`、`NoSuchKey` 或 `NotFound` 会被视作对象缺失；鉴权、网络或服务端错误
  保持为 CLI 失败，调用方不得将其误判为缺失。

## CDN

```toml
[object-storage.targets.archive.cdn]
provider = "tencent"
base_url = "https://cdn.example.com"
purge_on_upload = false
```

腾讯云 CDN provider 为 `tencent`：

| 命令 | 腾讯云 API |
|---|---|
| `cdn purge-url` | `PurgeUrlsCache` |
| `cdn purge-path` | `PurgePathCache` |
| `cdn prefetch` | `PushUrlsCache` |

`--keys` 会使用 `cdn.base_url` 构造 URL；`--urls` 直接使用完整 URL。CDN 可复用
target 的显式腾讯云 AK/SK；target 使用 profile 或默认链时，CDN 子表须单独配置凭据。

## 火山引擎 CDN

```toml
[object-storage.targets.kongdao-tos.cdn]
provider = "volcengine"
base_url = "https://cdn.example.com"
purge_on_upload = false
```

火山引擎 CDN provider 为 `volcengine`，复用 TOS target 的显式 AK/SK；也可在 CDN
子表单独配置成对的凭据。SDK 固定使用 CDN 公共服务地址、`cn-north-1` 签名区域。

| 命令 | 火山引擎 API / 类型 |
|---|---|
| `cdn purge-url` | `SubmitRefreshTask`，`type=file` |
| `cdn purge-path` | `SubmitRefreshTask`，`type=dir` |
| `cdn prefetch` | `SubmitPreloadTask` |
| `cdn status` | `DescribeContentTasks` |

- 刷新至少需要 `SubmitRefreshTask` 权限，查询结果需要 `DescribeContentTasks`；预热另需
  `SubmitPreloadTask`。真实提交成功只能证明提交权限，任务达到 `completed` 才证明 CDN
  已完成该任务。
- 目录 URL 自动补 `/`，默认 `--flush-type flush` 对应标记缓存过期。`delete` 对应接口的
  硬删除选项，需账号已开通相应白名单；它不会删除 TOS 源站对象。
- `--area` 是腾讯云参数，火山引擎预热不接受该参数。
- 官方默认额度为每天 10000 个文件 URL、50 个目录 URL，每次任务最多 100 个 URL；
  账号实际额度以控制台和 API 返回为准。

接口来源：[提交刷新任务](https://www.volcengine.com/docs/6454/70438?lang=zh)、
[查询刷新任务](https://www.volcengine.com/docs/6454/70437?lang=zh)。

## AWS CloudFront

```toml
[object-storage.targets.kongdao.cdn]
provider = "cloudfront"
base_url = "https://cdn.example.com"
distribution_id = "YOUR_DISTRIBUTION_ID"
purge_on_upload = false
```

CloudFront 复用所属 S3 target 的凭据（包括 session token）、profile 或默认凭据链；
不在 CDN 子表重复配置密钥。`base_url` 是用户访问 CDN 的根地址，通常与
`public_base_url` 一致。`distribution_id` 为 CloudFront 分配 ID，不能填 bucket 名。

- `cdn purge-url` 提交文件路径刷新；`cdn purge-path` 将目录转换为 `/dir/*`，
  两种 `flush-type` 在 CloudFront 中均执行 invalidation。不会删除 S3 对象。
- 完整 URL 必须与 `cdn.base_url` 的协议和主机匹配；保留查询参数，路径按需编码。
- `--keys` 是 CDN 根地址下的对象 key；若 target 有 prefix，手动刷新时需包含该 prefix。
- `--dry-run` 显示实际 invalidation 路径，不请求 AWS。
- CloudFront 不支持本工具的 `prefetch`；明确报错，不伪造预热成功。
- `cdn status --target kongdao --task-id ID --json` 查询状态；
  `submitted` 仅表示已提交，`completed` 才表示 AWS 报告完成。
- 配置 ID 后刷新无需 `ListDistributions`。需要该分配上的
  `cloudfront:CreateInvalidation` 和 `cloudfront:GetInvalidation` 权限。

路径与通配符语义见 [AWS 官方文档](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/invalidation-specifying-objects.html)。

## CDN 刷新额度与合并示例

核对日期：2026-09-20。刷新优先级与合并工作流见 SKILL.md 的 CDN 刷新策略。

### Amazon CloudFront

- 每月前 1000 个失效路径免费，额度按 AWS 账户下所有分配合计，超出按路径收费。
- `/project/*` 算一个路径，即使匹配数千个文件；`/*` 也算一个，但范围必须符合任务。
- 一次请求包含 100 个文件路径仍计 100 个路径；合并为一个目录通配符才计一个。
- 标签失效同样占用这 1000 个额度；本 skill 当前未实现标签失效。
- 不根据本地调用次数估算整个账户的剩余额度。

来源：[AWS 失效计费规则](https://docs.aws.amazon.com/zh_cn/AmazonCloudFront/latest/DeveloperGuide/PayingForInvalidation.html)。

### 腾讯云 CDN

采用相同的“汇总变更、优先目录、减少重复”的策略，但使用 `PurgePathCache`
提交目录 URL。URL 刷新不支持包含通配符的 URL，不照搬 CloudFront 的 `*` 参数。
官方接口文档列出的默认目录额度为境内、境外每日各 100 条，每次最多提交 500 条；
实际执行以账户当前额度为准，不套用 AWS 每月 1000 路径免费规则。
`flush` 刷新变更资源，`delete` 刷新全部资源；均不删除源站文件。

来源：[腾讯云缓存刷新](https://cloud.tencent.com/document/product/228/6299)、
[目录刷新 API](https://cloud.tencent.com/document/product/228/37871)。

### 火山引擎 CDN

采用相同的目录合并策略，通过 `SubmitRefreshTask` 的 `type=dir` 提交目录 URL。
官方默认额度为每天 50 个目录 URL；文件刷新默认每天 10000 个 URL。一次请求的文件或
目录 URL 上限均为 100 条。AWS 每月 1000 路径免费规则不适用。

来源：[火山引擎提交刷新任务](https://www.volcengine.com/docs/6454/70438?lang=zh)。

### 合并示例

本次变更 `project/video/a.mp4`、`project/video/b.mp4`，统一刷新 `project/video/`：

```bash
object-storage cdn purge-path --target kongdao --keys project/video/ --dry-run --json
object-storage cdn purge-path --target kongdao --keys project/video/ --json
```

CloudFront 最终提交 `/project/video/*`，计一个失效路径；将 target 换成腾讯云或
火山引擎目标时，同一命令提交 `https://配置的CDN域名/project/video/` 目录刷新。
此策略由 Agent 选择命令和汇总目录实现；CLI 不会自动将 `purge-url` 或
`purge_on_upload = true` 的逐文件刷新改为目录刷新。
