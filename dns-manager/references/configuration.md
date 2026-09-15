# dns-manager 配置说明

## 配置发现顺序

`dns-manager` 通过仓库共享的 `agent_config.py` 模板查找 `agent_config.toml`，顺序为：

1. 当前工作目录的 `agent_config.toml`
2. skill 根目录的 `agent_config.toml`
3. 从当前工作目录向上找到的最近 Git 根目录中的 `agent_config.toml`
4. 全局 `~/.agents/agent_config.toml`

命中第一个存在的文件后停止。CLI 的 `--config PATH` 可以显式指定文件；
显式路径不存在时报错退出，不会静默回退。

## Section 结构

```toml
[dns-manager]
provider = "tencentcloud"   # 必填：DNS 服务商，当前支持 tencentcloud

[dns-manager.tencentcloud]  # 每个服务商一个子表，key 与 provider 同名
secret_id = ""              # 可选：直接填密钥（不推荐写进文件）
secret_key = ""
secret_id_env = "TENCENTCLOUD_SECRET_ID"    # 推荐：从环境变量读取
secret_key_env = "TENCENTCLOUD_SECRET_KEY"
```

### 凭据解析规则

每个敏感字段按以下顺序解析，命中即止：

1. `*_env` 指定的环境变量（例如 `secret_id_env = "TENCENTCLOUD_SECRET_ID"`
   表示先读环境变量 `TENCENTCLOUD_SECRET_ID`）
2. TOML 中的字面值（`secret_id = "..."`）

两个字段（secret_id、secret_key）都必须解析成功，否则 `doctor` 与所有命令报错并提示
缺失字段与对应的环境变量名。凭据值不会出现在任何输出中，`doctor` 只报告
`credentials_resolved: {"secret_id": true, "secret_key": true}`。

### 腾讯云密钥建议

- 在访问管理（CAM）中创建子账号，只授予 `QcloudDNSPodFullAccess`（或更小的只读/记录级策略）。
- 子账号密钥写入 shell 配置或密钥管理器，通过环境变量注入。
- 示例见 skill 根目录 `agent_config.example.toml`。

## doctor 输出示例

```json
{
  "ok": true,
  "provider": "tencentcloud",
  "config_path": "/Users/me/project/agent_config.toml",
  "credentials_resolved": {"secret_id": true, "secret_key": true},
  "provider_class": "TencentCloudDns"
}
```

## 新增 Provider（开发者指南）

服务商抽象位于 `scripts/dns_manager/providers/`：

```text
providers/
├── __init__.py        # registry：name -> class
├── base.py            # DnsProvider 抽象基类
└── tencentcloud.py    # 腾讯云 DNSPod 实现
```

新增服务商（例如 aliyun）：

1. 新建 `providers/aliyun.py`，实现 `DnsProvider` 子类：
   - 类属性 `name = "aliyun"`（与 TOML 中 `provider` 值一致）；
   - 实现三个方法：`list_records` / `upsert_record` / `delete_record`；
   - 全部操作必须幂等：`upsert_record` 在记录完全一致时返回 `action="unchanged"`
     且不发起写请求；`delete_record` 在记录不存在时返回 `action="skipped"`。
   - HTTP 层封装为可注入的 transport（参考 `tencentcloud.py` 的
     `Transport` 类型），便于单元测试离线运行。
2. 在 `providers/__init__.py` 的 `_REGISTRY` 中注册。
3. 在 `agent_config.example.toml` 增加 `[dns-manager.aliyun]` 示例块，
   并更新本文件的 Section 结构说明。
4. 在 `tests/` 中补充该 provider 的幂等逻辑测试（用 fake transport，不联网）。

CLI 与配置层无需改动：`provider` 字段自动路由到新实现。

## 已知的服务商细节（踩坑记录）

### 腾讯云 DNSPod

- `CreateRecord`/`ModifyRecord` 的 `RecordLine` 必须传中文 `默认`（默认线路），
  传英文 `"Default"` 会被拒绝且报错不直观。
- `DescribeRecordList` 在 zone 无记录时返回业务错误码
  `ResourceNotFound.NoDataOfRecord`（HTTP 200），实现中已把它映射为空列表。
- 签名为 TC3-HMAC-SHA256（`dnspod.tencentcloudapi.com`，API 版本 `2021-03-23`），
  本实现只用标准库，无第三方依赖。
