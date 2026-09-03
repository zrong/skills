# 配置

## 查找顺序

`matting` 读取共享 `agent_config.toml`，顺序固定为：

1. `--config PATH`；
2. `MATTING_CONFIG`；
3. 当前工作目录；
4. skill 根目录；
5. 当前工作目录向上的最近 Git 根目录；
6. `~/.agents/agent_config.toml`。

显式路径或 `MATTING_CONFIG` 指向的文件不存在时立即失败，不继续回退。

## `[matting]`

| 字段 | 必需 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `base_url` | 是 | - | matting-api 根 URL，不含末尾 `/` |
| `timeout` | 否 | `300` | 单次 HTTP 请求超时秒数 |
| `poll_interval` | 否 | `2` | 任务轮询间隔秒数，范围 `0.1..60` |
| `max_wait_seconds` | 否 | `900` | 整个异步任务最长等待时间 |
| `default_model` | 否 | 空 | 自动选择同一方法时优先尝试的模型；仍受实时能力与兼容关系约束 |
| `max_input_bytes` | 否 | `52428800` | 输入文件大小上限 |
| `max_pixels` | 否 | `100000000` | 解码像素上限 |
| `api_key` / `api_key_env` | 否 | 空 | 可选网关认证；不要提交真实 key |
| `auth_header` | 否 | `Authorization` | key 所在 header |
| `auth_scheme` | 否 | `Bearer` | header 值前缀；设为空可直接发送 key |

`[matting.headers]` 可配置额外 header，值支持 `${ENV_NAME}`。诊断输出只报告配置文件路径，不回显 header 值。

## 模型兼容声明

新版服务若在 `/api/capabilities` 返回 `model_methods`，它是首选真值。旧服务只返回 `methods` 与 `models` 两个独立列表，CLI 会使用已知 matting-api 协议映射。

对自定义模型可补充：

```toml
[matting.models."custom-birefnet"]
methods = ["birefnet", "birefnet_luma_restore"]
```

该声明不会授权服务未广告的模型或算法，只用于证明两者兼容。未知组合不会被自动或显式选择；需要先由服务能力或本地配置提供兼容映射，服务仍会做最终校验。
