# 配置参考

## 结构

`[filebrowser]` 保存默认选择和暂存目录；每个 FileBrowser 连接位于 `sources.<name>`，每个上传目标位于 `targets.<name>`。名称可以由 CLI 的 `--source` 和 `--target` 选择。

```toml
[filebrowser]
default_source = "production"
default_target = "archive"
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

[filebrowser.targets.archive]
adapter = "s3"
bucket = "archive-bucket"
region = "ap-guangzhou"
endpoint_url = "https://cos.ap-guangzhou.myqcloud.com"
public_base_url = "https://cdn.example.com"
prefix = "filebrowser"
access_key_id = ""
access_key_id_env = "ARCHIVE_S3_ACCESS_KEY_ID"
secret_access_key = ""
secret_access_key_env = "ARCHIVE_S3_SECRET_ACCESS_KEY"
session_token = ""
session_token_env = ""
profile = ""
addressing_style = "virtual"
storage_class = ""
multipart_threshold_bytes = 8388608
multipart_chunksize_bytes = 8388608
max_concurrency = 4
verify_tls = true
```

## 多 S3 配置

继续增加 target 表即可。每个 target 独立拥有 bucket、endpoint、prefix 和凭据来源：

```toml
[filebrowser.targets.aws-backup]
adapter = "s3"
bucket = "company-backup"
region = "ap-southeast-1"
profile = "company-backup"
prefix = "filebrowser"
addressing_style = "auto"

[filebrowser.targets.minio-local]
adapter = "s3"
bucket = "media"
region = "us-east-1"
endpoint_url = "https://minio.example.com"
access_key_id_env = "MINIO_ACCESS_KEY"
secret_access_key_env = "MINIO_SECRET_KEY"
addressing_style = "path"
verify_tls = true
```

## 凭据优先级

- FileBrowser token：配置中的 `token`，否则读取 `token_env` 指定的环境变量。
- S3：`profile`；或成对的 access/secret 配置值；或 `*_env` 指定的环境变量；均未设置时使用 boto3 默认凭据链。
- 不允许同时设置 `profile` 和显式 access/secret。
- 只要声明了显式 access/secret，缺少对应环境变量就会直接失败，不会退回默认凭据链。
- `session_token` 只能与显式 access/secret 一起声明。
- 示例文件只能包含空值和环境变量名，不能提交真实凭据。

## S3 兼容端点

- AWS S3：`endpoint_url` 可留空，`addressing_style = "auto"`。
- 腾讯云 COS、阿里云 OSS、火山 TOS：填写其 S3 兼容 endpoint，并按服务要求选择 `virtual` 或 `path`。
- `public_base_url` 只用于构造结果中的访问 URL，不作为 API endpoint；私有 bucket 可留空。
- `prefix` 是所有对象 key 的相对前缀，不能包含 `..`。

## 传输语义

- 默认 object key 为 FileBrowser 文件路径去掉开头 `/` 后的值，再添加 S3 target `prefix`。
- `--key` 覆盖 FileBrowser 派生部分，但仍会添加 target `prefix`。
- multipart 参数由 boto3 `TransferConfig` 使用；上传完成后通过 `head_object` 校验对象大小。
- skill 优先使用 `/api/resources/download`，服务器返回 404/405 时兼容回退 `/api/raw`。
- `put` 直接将本地文件流式上传至 FileBrowser；大于 `upload_chunk_bytes` 的文件使用
  `X-File-Chunk-Offset` / `X-File-Total-Size` 顺序分块上传。
- `put` 默认拒绝同名远端文件；只有显式传 `--overwrite` 才会替换。上传成功后读取远端
  元数据并校验文件大小。
