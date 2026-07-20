# 配置与调用边界

## v2 结构

完整模板见 `../agent_config.example.toml`。层级固定为：

```toml
[image-generation]
default_provider = "primary"

[image-generation.providers.primary]
default_endpoint = "openai"

[image-generation.providers.primary.endpoints.openai]
adapter = "openai"
base_url = "https://api.openai.com/v1"
api_key = "${OPENAI_API_KEY}"
default_model = "gpt-image-1.5"

[image-generation.providers.primary.endpoints.openai.models."gpt-image-1.5"]
adapter = "openai"
operations = ["generate", "edit"]
capabilities = ["multi_reference", "mask", "size", "quality"]
max_references = 16
max_outputs = 4
```

每个 endpoint 必须独立包含：

- `adapter`：`openai`、`gemini`、`seedream` 之一；
- `base_url`：包含 API 版本前缀，不带末尾 `/`；
- `api_key` 或 `api_key_env`：只属于该 endpoint；
- `auth`：Gemini 可选 `x-goog-api-key`（Google 原生）或 `bearer`（兼容网关）；
- `models`：这个 key/base URL 明确获准调用的精确模型表。

`api_key` 可以直接保存本地 key，也可以写成 `${ENV_NAME}`。更推荐 `api_key_env = "ENV_NAME"` 或 `${ENV_NAME}`，示例和提交中不能放真实 key。

已有本机配置若暂时需要复用 provider 层旧 key，可在 endpoint 显式写 `api_key = "@provider"`。这只是无泄露迁移引用；新配置应为每个 endpoint 使用独立 key/环境变量。

## 模型 policy

必填：

- `operations`：`generate`、`edit` 的非空子集。
- `adapter`：必须显式声明，并与所属 endpoint 的 adapter 完全一致。

常用可选字段：

- `capabilities`：允许 CLI 发送的特性；
- `api_model`：配置别名对应的远端模型 ID；默认等于表名；
- `sizes`、`qualities`、`output_formats`：非空时形成精确值 allowlist；
- `max_references`、`max_outputs`：硬限制；
- `options`：adapter 专属路径和固定 payload。

示例：

```toml
[image-generation.providers.primary.endpoints.openai.models."internal-image"]
adapter = "openai"
api_model = "gpt-image-1.5"
operations = ["generate"]
capabilities = ["size"]
max_references = 0
max_outputs = 1

[image-generation.providers.primary.endpoints.openai.models."internal-image".options]
generate_path = "/images/generations"
payload = { response_format = "b64_json" }
```

`options.payload` 只用于 endpoint 要求的稳定兼容参数，不应用它绕开 capability 校验。

## 严格行为

以下行为全部在网络请求前拒绝：

- 模型不在当前 endpoint 的 `models`；
- 模型未允许本次 operation；
- 显式 CLI 参数缺少对应 capability；
- 参数值不在模型的 sizes/qualities/output_formats；
- 参考图或输出数量超过 policy；
- default model 不在当前 endpoint allowlist。

`imggen models` 会显示三类状态：配置且远端可见、已配置但远端未列出、远端可见但被本地阻止。第三类不会自动加入配置。

## 配置查找顺序

1. `--config /absolute/path/agent_config.toml`
2. `IMAGEGEN_CONFIG`
3. 当前目录 `agent_config.toml`
4. skill 目录 `agent_config.toml`
5. 当前 git 根目录 `agent_config.toml`

显式路径不存在时不回退到其他文件，避免意外使用另一项目的 key。

## 从 v1 迁移

旧格式把 key 放在 `[image-generation.providers.NAME]`，endpoint 只有 base URL。v2 不接受这种继承：

1. 将 key 或环境变量引用分别放入每个 endpoint；
2. 为 endpoint 增加明确 `adapter`；
3. 把允许的模型逐个写成 `models."MODEL"` 子表；
4. 为每个模型声明 operation、capability 和数量限制；
5. 用 `imggen list` 检查静态配置，再用 `imggen models` 对照远端。

迁移时不能因为远端 `/models` 返回某模型就自动授权。
