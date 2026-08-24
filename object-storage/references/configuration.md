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

## CDN

```toml
[object-storage.targets.archive.cdn]
provider = "tencent"
base_url = "https://cdn.example.com"
purge_on_upload = false
```

目前 CDN provider 支持 `tencent`：

| 命令 | 腾讯云 API |
|---|---|
| `cdn purge-url` | `PurgeUrlsCache` |
| `cdn purge-path` | `PurgePathCache` |
| `cdn prefetch` | `PushUrlsCache` |

`--keys` 会使用 `cdn.base_url` 构造 URL；`--urls` 直接使用完整 URL。CDN 可复用
target 的显式腾讯云 AK/SK；target 使用 profile 或默认链时，CDN 子表须单独配置凭据。
