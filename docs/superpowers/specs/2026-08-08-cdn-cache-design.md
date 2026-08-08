# CDN 缓存管理（刷新 URL / 刷新目录 / 预热）设计

- 日期：2026-08-08
- 范围：`filebrowser` skill
- 状态：待实现

## 1. 背景与目标

`filebrowser` skill 的 `upload` 流程把 FileBrowser 文件下载后上传到 S3 兼容存储（AWS S3 / 腾讯云 COS / 阿里云 OSS 等）。当 bucket 前面挂了 CDN，上传（尤其覆盖上传同名对象）后，CDN 边缘节点仍可能命中旧缓存，导致终端用户看不到最新内容。

本设计为该 skill 增加 **CDN 缓存管理**能力，首批支持 **腾讯云 CDN**，提供三个**互相独立、语义不同**的功能：

1. **刷新 URL**（`PurgeUrlsCache`）：清除指定 URL 在 CDN 全网节点的缓存。覆盖上传后让旧缓存失效。
2. **刷新目录**（`PurgePathCache`）：清除指定目录路径下资源的 CDN 缓存。
3. **预热**（`PushUrlsCache`）：主动把指定 URL 的资源拉取到 CDN 边缘节点缓存，使首次访问即命中。

> 这三个是**不同的 API、不同的语义**，必须在代码与文档中分开建模，不可笼统地称为「CDN 刷新」。

### 非目标

- 不支持 AWS CloudFront、阿里云、华为云等其他 CDN（架构预留 provider 扩展点，见 §14）。
- 不轮询刷新/预热进度（`fire-and-forget`），不提供 `--wait` 类选项。提交后把 TaskId 和提交列表交还用户，提醒其自行测试。
- 不做 CDN 配额管理、超额重试或退避；超额由腾讯云返回错误，原样透传给用户。
- 暂不支持腾讯云临时安全凭据（`session_token`/`Token`），仅支持长期 AK/SK。

## 2. 关键事实（腾讯云 CDN API 3.0）

请求域名：`cdn.tencentcloudapi.com`。CDN 为全局服务，SDK client 的 region 传空字符串。

| 功能 | API | 必选入参 | 单次上限 | 每日额度 |
|------|-----|----------|---------|---------|
| 刷新 URL | `PurgeUrlsCache` | `Urls: list[str]`（每条须带 `http(s)://`） | 1000 | 按账号额度 |
| 刷新目录 | `PurgePathCache` | `Paths: list[str]`（每条须以 `/` 结尾）+ `FlushType`（`flush`/`delete`） | 10 | 100/加速区域 |
| 预热 | `PushUrlsCache` | `Urls: list[str]`；可选 `Area`（`mainland`/`overseas`） | 500 | 1000/加速区域 |

- `FlushType`：`flush` = 仅刷新有变更的资源（省带宽）；`delete` = 清除目录下全部资源（更彻底，可能短时大量回源）。
- 三者均返回 `TaskId`，可用 `DescribePurgeTasks` 查询进度（**本设计不查询**）。
- Python SDK：`tencentcloud-sdk-python-cdn`（精简包），导入路径 `tencentcloud.cdn.v20180606`。异常类型 `tencentcloud.common.exception.TencentCloudSDKException`。

### 凭据复用与 CAM 权限（重要）

腾讯云 CDN 与 COS 共用统一的 **CAM 访问管理**账号体系，一组 AK/SK 是账号级凭证，**不是 COS 专属**。因此 `upload` 目标里 bucket 的 AK/SK **可直接复用来调 CDN API**，无需为 CDN 单独申请密钥。

但前提：该 AK/SK 对应的子用户/角色在 CAM 中被授予相应权限：

- `cdn:PurgeUrlsCache`
- `cdn:PurgePathCache`
- `cdn:PushUrlsCache`

一个仅授予 COS 权限（如 `QcloudCOSFullAccess`）的子用户，用其 AK/SK 调上述接口会返回 403。须在 CAM 控制台为该子用户附加上述最小权限策略。

## 3. 配置语义澄清

- `public_base_url`（**已有字段，保持不变**）：bucket 的自定义/源站域名，拼出直连源站的 `public_url`，**与 CDN 无关**。即使示例里写作 `https://cdn.example.com`，其语义也只是「bucket 的公开访问基址」。
- `cdn.base_url`（**新增，必需**）：CDN 加速域名，是刷新/预热的真正目标。**不可回退**到 `public_base_url`，二者是不同层、通常不同域名。

## 4. 架构

与现有 `targets.py`（`UploadTarget` Protocol + `S3Target` 实现 + `build_target` 工厂）对称，新增 `cdn.py` 模块：

- `CdnCacheManager(Protocol)`：声明三个方法（见 §6）。
- `TencentCdnCacheManager`：腾讯云实现，**lazy import** SDK。
- `build_cdn_cache_manager(target_config) -> CdnCacheManager | None`：无 cdn 配置返回 `None`。

刷新/预热属于 CDN 控制面，**不塞进 `S3Target`**（保持存储层单一职责），由 `TransferService` 编排层（`upload` 自动触发）和独立 CLI 命令（手动触发）调用。

## 5. 配置模型（`models.py`）

新增 `CdnTargetConfig`、`CdnTaskResult`；扩展 `S3TargetConfig`、`UploadResult`。

```python
@dataclass(frozen=True, slots=True)
class CdnTargetConfig:
    name: str                                  # 取自所属 target 名
    provider: str                              # "tencent"
    base_url: str                              # CDN 域名，必需，http(s)
    purge_on_upload: bool = False              # upload 成功后自动 purge_url
    access_key_id: SecretValue = field(default_factory=SecretValue, repr=False)
    secret_access_key: SecretValue = field(default_factory=SecretValue, repr=False)


@dataclass(frozen=True, slots=True)
class CdnTaskResult:
    operation: str        # "purge_url" | "purge_path" | "prefetch"
    status: str           # "submitted" | "failed"
    task_id: str          # 腾讯云 TaskId；failed 时为 ""
    targets: list[str]    # 本次提交的 urls 或 paths
    error: str            # failed 时的错误信息；成功为 ""
```

- `S3TargetConfig` 增加 `cdn: CdnTargetConfig | None = None`。
- `UploadResult` 增加 `cdn_task: CdnTaskResult | None = None`。
- `type TargetConfig = S3TargetConfig` 别名不变（cdn 是 S3TargetConfig 的子配置，不引入新的顶层 target 类型）。

## 6. `cdn.py` 模块

### 6.1 Protocol

```python
class CdnCacheManager(Protocol):
    name: str

    def purge_url(self, urls: list[str]) -> CdnTaskResult: ...

    def purge_path(
        self, paths: list[str], *, flush_type: Literal["flush", "delete"]
    ) -> CdnTaskResult: ...

    def prefetch(self, urls: list[str], *, area: str = "") -> CdnTaskResult: ...

    def build_url(self, object_key: str) -> str: ...
```

`area` 取值：`""`（不传，默认）/`"mainland"`/`"overseas"`。

### 6.2 `TencentCdnCacheManager`

```python
class TencentCdnCacheManager:
    def __init__(self, config: CdnTargetConfig, *, client: CdnClientProtocol | None = None) -> None: ...
```

- 构造时 **lazy import** `tencentcloud.common.credential`、`tencentcloud.cdn.v20180606.cdn_client / models`，建立 `CdnClient(credential.Credential(secret_id, secret_key), "")`。
- `client` 参数供测试注入 fake（与 `S3Target` 的 `client` 注入点对称）。生产路径为 `None` 时内部建真实 client。
- 三个方法各自：构造对应 `models.XxxRequest` → 设字段（`Urls` / `Paths`+`FlushType` / `Urls`+可选 `Area`）→ 调用 → 取 `resp.TaskId` → 返回 `CdnTaskResult(operation=..., status="submitted", task_id=..., targets=[...], error="")`。
- 任何异常（`TencentCloudSDKException` 及其他 `Exception`）→ 捕获 → 返回 `CdnTaskResult(status="failed", task_id="", error=str(exc))`，**不抛出**。
- `build_url(object_key)`：`f"{self.config.base_url}/{quote(object_key, safe='/')}"`（与 `S3Target.upload` 拼 `public_url` 的方式一致）。

### 6.3 `build_cdn_cache_manager`

```python
def build_cdn_cache_manager(target_config: S3TargetConfig) -> CdnCacheManager | None:
    if target_config.cdn is None:
        return None
    cdn = target_config.cdn
    if cdn.provider != "tencent":
        raise ConfigurationError(f"Unsupported CDN provider: {cdn.provider}")
    return TencentCdnCacheManager(cdn)
```

AK/SK 明文解析在构造 `TencentCdnCacheManager` 时完成（`config.access_key_id.resolve(...)` / `secret_access_key.resolve(...)`，`required=True`），与 `S3Target._build_client` 的解析时机一致。复用 target SecretValue 时，明文在运行时从环境变量读取。

## 7. 配置解析（`config.py`）

新增 `_parse_cdn(name, table, target_ak, target_sk) -> CdnTargetConfig | None`，由 `_parse_target` 在解析完 target 的 ak/sk 后调用。

解析规则：

1. `table` 无 `cdn` 键 → 返回 `None`（不启用）。
2. `provider`：必选字符串，必须 ∈ `{"tencent"}`，否则 `ConfigError`。
3. `base_url`：必选，经 `_http_url(required=True)` 校验为 http(s)。
4. `purge_on_upload`：`_bool`，默认 `False`。
5. **凭据来源**（核心约束，见 §7.1）：
   - cdn 子表声明了 `access_key_id`/`secret_access_key`（direct 或 `*_env`）→ 用 cdn 自己的 `SecretValue`（沿用 `_secret` 解析 `access_key_id` + `access_key_id_env`）。
   - 否则 → 复用 target 的 `access_key_id` / `secret_access_key`（直接引用同一 `SecretValue` 对象）。

### 7.1 profile 模式边界

`upload` target 可能用 AWS `profile` 模式（无显式 AK/SK）。腾讯云 SDK 需要明文 `SecretId/SecretKey`，无法从 boto3 profile 复用。因此：

- 若 cdn 子表未声明凭据，**且** target 的 `access_key_id.declared` 为 `False`（即 target 走 profile 或默认凭据链）→ `_parse_cdn` 抛 `ConfigError`，提示：「target 使用 profile/默认凭据链，无法复用给 CDN；请在 `[filebrowser.targets.<name>.cdn]` 显式配置 `access_key_id`/`secret_access_key`（或对应 `*_env`）」。
- 若 target 的 ak/sk 已 declared（显式或 env）→ 复用，运行时 `resolve` 缺失环境变量会按现有机制报错。

校验在解析期（fail fast），不拖到运行时。

## 8. 编排集成（`transfer.py`）

- `TransferService.__init__` 增加 `cdn_factory: Callable[[S3TargetConfig], CdnCacheManager | None] = build_cdn_cache_manager`，与 `target_factory` 对称，便于测试注入。
- `upload()` 在 `target.upload(...)` 成功返回 `UploadResult` 后：
  ```python
  cdn_config = target_config.cdn
  if cdn_config is not None:
      manager = self._cdn_factory(target_config)
      if cdn_config.purge_on_upload and manager is not None:
          url = manager.build_url(plan.object_key)
          result = replace(result, cdn_task=manager.purge_url([url]))
  return result
  ```
- 刷新异常已被 `TencentCdnCacheManager` 内部转为 `failed`，**绝不阻断 upload**，`UploadResult` 照常返回。

## 9. CLI（`cli.py`）

### 9.1 新增 `cdn` 子命令组

在 `subparsers` 下新增三个子命令，各自：`load_skill_config` → `config.target(--target)` 取 `S3TargetConfig` → `build_cdn_cache_manager(target_config)`（为 `None` 则报错「target X 未配置 cdn」）→ 调对应方法 → 输出 `CdnTaskResult`。

| 子命令 | 参数 | 说明 |
|--------|------|------|
| `cdn purge-url` | `--target`、`--urls`（nargs+，与 `--keys` 二选一）、`--keys`（nargs+，用 `build_url` 拼）、`--json`、`--dry-run` | 刷新 URL |
| `cdn purge-path` | `--target`、`--paths`（nargs+）、`--flush-type`（`flush`/`delete`，默认 `flush`）、`--json`、`--dry-run` | 刷新目录 |
| `cdn prefetch` | `--target`、`--urls`（nargs+）、`--area`（`""`/`mainland`/`overseas`，默认不传）、`--json`、`--dry-run` | 预热 |

- `purge-path` 的 `--paths`：对不以 `/` 结尾的条目自动补 `/`（腾讯云要求目录以 `/` 结尾），并在输出里展示规范化后的路径。
- `--keys`（仅 `purge-url`）拼出的 URL 与 `purge_on_upload` 自动触发用的是同一 `build_url`。
- 输出（非 JSON）：`operation / status / task_id / targets`，并打印提醒行：「{operation} 已提交（TaskId: ...），CDN 通常 5 分钟内生效，请稍后自行访问测试。」
- 输出（`--json`）：`CdnTaskResult` 经 `asdict` 序列化。
- `--dry-run`：打印「将提交的 targets」与「将调用的 API」，不实际调用腾讯云。

### 9.2 `upload` 命令输出

`upload` 成功后若 `result.cdn_task` 非空：
- `--json`：`UploadResult` 经 `asdict` 自然包含 `cdn_task`。
- 非 JSON：追加打印 `cdn_task` 的 `operation/status/task_id` 与被刷新的 URL。

### 9.3 `list` / `doctor` 输出

`_summary` 的 targets 条目增加 `cdn_provider`（未配为 `""`）与 `cdn_purge_on_upload`，便于诊断。

## 10. 错误处理与退出码

两种入口语义不同：

- **独立 `cdn` 子命令**：CDN 操作是主操作。`failed` → 打印错误 → **退出码 1**。
- **`upload` 自动触发**（`purge_on_upload`）：刷新是附加动作。无论刷新成败，**退出码只反映 upload**（上传成功 0、失败 1）。刷新 `failed` 仅在 `--json` 的 `cdn_task` 与非 JSON 的 stderr warning 中体现。

`build_cdn_cache_manager` 抛出的 `ConfigurationError`（如 provider 不支持、profile 模式无凭据）经 CLI 现有 `except` 分支捕获，退出码 1。

## 11. 依赖

- `scripts/pyproject.toml` 增加 `tencentcloud-sdk-python-cdn`（CDN 精简包；若该精简包在实现时不可用，回退到完整包 `tencentcloud-sdk-python`，导入路径不变）。运行 `uv lock` 更新 `uv.lock`。
- **lazy import**：`cdn.py` 模块顶部**不** import `tencentcloud.*`；仅在 `TencentCdnCacheManager.__init__` 内 import。未配置 cdn 的用户零额外加载与零运行时依赖感知。

## 12. 文档更新

- `agent_config.example.toml`：在现有 target 示例下增加 `[filebrowser.targets.archive.cdn]` 子表示例（`provider`/`base_url`/`purge_on_upload`/凭据注释），并新增一个含 `purge_on_upload = true` 的用例注释。
- `references/configuration.md`：新增「CDN 缓存管理」章节，覆盖：三功能区别与适用场景、CLI 三子命令用法、`public_base_url` 与 `cdn.base_url` 的区别、各 API 单次/每日额度、`flush`/`delete` 含义、CAM 权限要求、profile 模式约束。
- `SKILL.md`：在能力说明中增加「上传后可自动刷新/手动刷新 URL/目录、预热腾讯云 CDN」一句与触发条件。
- 按项目规范（`CLAUDE.md` §3），提交时同步更新 `README.md` 更新记录并打 CalVer 标签（由 `git-commit` skill 处理）。

## 13. 测试计划

不联网，全部用注入的 fake client/manager。

- **`tests/test_cdn.py`（新增）**
  - fake `CdnClientProtocol`：验证 `purge_url` 设 `Urls`、`purge_path` 设 `Paths`+`FlushType`、`prefetch` 设 `Urls`+`Area`。
  - 成功路径：返回 `status="submitted"`、`task_id` 取自 fake 响应、`targets` 回显。
  - 异常路径：fake 抛 `TencentCloudSDKException` 与普通 `Exception` → `status="failed"`、`error` 非空、`task_id=""`、不抛出。
  - `build_url`：前缀拼接与 `quote`。
- **`tests/test_config.py`（增量）**
  - 解析 `[cdn]` 子表：provider/base_url/purge_on_upload。
  - `provider` 非 `tencent` → `ConfigError`；`base_url` 缺失或非 http(s) → `ConfigError`。
  - 凭据回退：cdn 未声明 → 复用 target ak/sk。
  - profile 模式（target ak/sk 未 declared）且 cdn 未声明凭据 → `ConfigError`。
- **`tests/test_transfer_and_cli.py`（增量）**
  - `upload` + `purge_on_upload=true` + 注入 fake `cdn_factory` → `UploadResult.cdn_task` 为 `submitted`，URL 为 `build_url(object_key)`。
  - fake manager 返回 `failed` → `UploadResult` 仍正常返回，upload 不抛。
  - `cdn` 三子命令的参数解析（`--keys` 拼 URL、`--flush-type` 默认 `flush`、`--paths` 自动补 `/`、`--area`）、`--dry-run` 不调用、退出码（成功 0 / failed 1）。

## 14. 未来扩展

- 新增 provider：实现新的 `CdnCacheManager`（如 AWS CloudFront 用 `create_invalidation`、阿里云用 `RefreshObjectCaches`/`PushObjectCache`），在 `build_cdn_cache_manager` 按 `provider` 分发，`_parse_cdn` 放开 provider 白名单。三个方法已在同一 Protocol，新操作（如查询进度）可平级新增，无需重构现有抽象。
- 临时安全凭据：若需要，扩展 `CdnTargetConfig` 携带 token 并在 `credential.Credential` 传入。
